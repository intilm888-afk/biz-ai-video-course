#!/usr/bin/env python3
"""Conservative object-based upstream sync. Never executes upstream files."""
import argparse
import fnmatch
import hashlib
import json
from pathlib import Path
import re
import subprocess

import yaml

STATE = '.safe-sync/state.json'
REPORT = '.safe-sync/report.md'
REGULAR = {'100644', '100755'}
HARD_PROTECTED = ('.github', '.agents', '.safe-sync', 'scripts', 'tests', 'docs',
                  'AGENTS.md', 'CLAUDE.md', '.gitignore', '.gitattributes')


def git(*args):
    return subprocess.check_output(['git', *args])


def tree(ref):
    result = {}
    for record in git('ls-tree', '-rz', ref).split(b'\0'):
        if record:
            meta, path = record.split(b'\t', 1)
            mode, kind, oid = meta.decode().split()
            result[path.decode('utf-8')] = [mode, oid]
    return result


def blob(entry):
    return git('cat-file', 'blob', entry[1])


def safe_path(path):
    p = Path(path)
    if p.is_absolute() or any(x in ('..', '.git') for x in p.parts):
        raise ValueError(f'unsafe path: {path!r}')
    for parent in [p, *p.parents]:
        if parent.is_symlink():
            raise ValueError(f'symlink path: {path!r}')
        if parent != p and parent.exists() and not parent.is_dir():
            raise ValueError(f'parent is a file: {path!r}')
    return p


def protected(path, patterns):
    if any(path == p or path.startswith(p + '/') for p in HARD_PROTECTED):
        return True
    # All hidden configuration is opt-in, except the teacher's skill resources.
    if any(p.startswith('.') for p in Path(path).parts):
        if not path.startswith(('.claude/skills/', '.claude/lib/')):
            return True
        if any(p.startswith('.') for p in Path(path).parts[2:]):
            return True
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    mapping = {}
    for k, v in node.value:
        key = loader.construct_object(k)
        if key in mapping:
            raise ValueError(f'duplicate YAML key: {key}')
        mapping[key] = loader.construct_object(v)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def frontmatter(data, folder, repair=False):
    text = data.decode('utf-8')
    lines = text.splitlines()
    if not lines or lines[0] != '---':
        raise ValueError('missing YAML frontmatter')
    try:
        end = lines.index('---', 1)
    except ValueError:
        raise ValueError('missing closing YAML delimiter') from None
    header_lines = lines[1:end]
    if repair:
        # Only repair an unquoted, single-line description; ambiguous YAML fails closed.
        fixed = []
        for line in header_lines:
            if line.startswith('description: '):
                value = line[len('description: '):]
                if value and value[0] not in '\"\'|>{[&*!':
                    line = 'description: ' + json.dumps(value, ensure_ascii=False)
            fixed.append(line)
        header_lines = fixed
    meta = yaml.load('\n'.join(header_lines), Loader=UniqueLoader)
    if not isinstance(meta, dict):
        raise ValueError('frontmatter must be a mapping')
    for key in ('name', 'description'):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise ValueError(f'{key} must be a nonempty string')
    if repair:
        meta['name'] = folder
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', meta['name']) or len(meta['name']) > 64:
        raise ValueError('invalid skill name')
    if meta['name'] != folder:
        raise ValueError('name does not match skill directory')
    return meta, '\n'.join(lines[end + 1:])


def convert(data, folder):
    meta, body = frontmatter(data, folder, repair=True)
    # Codex supports name/description; discard Claude-specific execution fields.
    header = yaml.safe_dump({k: meta[k] for k in ('name', 'description')},
                            allow_unicode=True, sort_keys=False).rstrip()
    return ('---\n' + header + '\n---\n' + body.replace('.claude/skills/', '.agents/skills/') + '\n').encode()


def fingerprint(files):
    return {p: [mode, hashlib.sha256(data).hexdigest()]
            for p, (mode, data) in sorted(files.items())}


def disk_skill(root):
    safe_path(str(root))
    if root.exists() and not root.is_dir():
        raise ValueError('skill path is not a directory')
    files = {}
    for p in sorted(root.rglob('*')):
        safe_path(str(p))
        if p.is_file():
            files[p.relative_to(root).as_posix()] = [
                '100755' if p.stat().st_mode & 0o111 else '100644', p.read_bytes()]
    return files


def write_file(path, mode, data):
    p = safe_path(path)
    if p.exists() and not p.is_file():
        raise ValueError(f'not a regular file: {path}')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    p.chmod(0o755 if mode == '100755' else 0o644)


def validate_all():
    errors = []
    count = 0
    for base in (Path('.claude/skills'), Path('.agents/skills')):
        if not base.exists():
            continue
        try:
            safe_path(str(base))
        except ValueError as e:
            errors.append(str(e))
            continue
        for folder in sorted(base.iterdir()):
            if folder.name.startswith('.'):
                continue
            try:
                safe_path(str(folder))
                if not folder.is_dir():
                    errors.append(f'{folder}: not a skill directory')
                    continue
                files = disk_skill(folder)
                frontmatter(files['SKILL.md'][1], folder.name)
                count += 1
            except (ValueError, KeyError, UnicodeError, yaml.YAMLError) as e:
                errors.append(f'{folder}: {e}')
    return count, errors


def sync(upstream, base, patterns=()):
    current, incoming = tree('HEAD'), tree(upstream)
    state_path = safe_path(STATE)
    previous_state = state_path.read_text() if state_path.exists() else None
    state = json.loads(previous_state) if previous_state else {
        'version': 1, 'baseline': tree(base), 'generated': {}}
    if state.get('version') != 1:
        raise ValueError('unsupported sync state')
    baseline = state['baseline']
    report = {k: [] for k in ('raw_added', 'raw_modified', 'raw_deleted', 'protected',
                              'skills_added', 'skills_modified', 'skills_deleted', 'conversion_notes', 'conflicts')}
    # A Claude skill is atomic: do not partially update a locally changed skill.
    changed_skills = set()
    for p in set(baseline) | set(incoming):
        if p.startswith('.claude/skills/') and len(Path(p).parts) >= 4:
            if baseline.get(p) != incoming.get(p):
                changed_skills.add(Path(p).parts[2])
    blocked = set()
    for name in changed_skills:
        prefix = f'.claude/skills/{name}/'
        old = {p: v for p, v in baseline.items() if p.startswith(prefix)}
        local = {p: v for p, v in current.items() if p.startswith(prefix)}
        new = {p: v for p, v in incoming.items() if p.startswith(prefix)}
        untracked = [str(p) for p in Path(prefix).rglob('*') if p.is_file() and str(p) not in current]
        try:
            safe_path(prefix.rstrip('/'))
            for path, entry in new.items():
                safe_path(path)
                if entry[0] not in REGULAR:
                    raise ValueError('unsafe symlink/submodule in upstream skill')
                if old.get(path) != entry and protected(path, patterns):
                    raise ValueError(f'protected resource in changed skill: {path}')
            if local != old and local != new or untracked:
                raise ValueError('local skill differs from baseline (including added files)')
        except ValueError as e:
            blocked.add(name)
            report['conflicts'].append(f'{prefix}: {e}')
    for p in sorted(set(baseline) | set(incoming)):
        old, new, local = baseline.get(p), incoming.get(p), current.get(p)
        if old == new:
            continue
        if protected(p, patterns):
            report['protected'].append(p)
            continue
        if p.startswith('.claude/skills/') and Path(p).parts[2] in blocked:
            continue
        try:
            dest = safe_path(p)
            if local != old and local != new:
                raise ValueError('local modification/addition/deletion differs from baseline')
            if new and new[0] not in REGULAR:
                raise ValueError('symlink/submodule is not imported')
            if local is None and dest.exists():
                raise ValueError('untracked local path exists')
            if new is None:
                # All deletions are review-only, preserving both raw and Codex work.
                report['raw_deleted'].append(p + ' (retained; manual review)')
                continue
            if local != new:
                write_file(p, new[0], blob(new))
                report['raw_added' if old is None else 'raw_modified'].append(p)
            baseline[p] = new
        except (ValueError, OSError) as e:
            report['conflicts'].append(f'{p}: {e}')
    groups = {}
    for p, entry in incoming.items():
        parts = Path(p).parts
        if len(parts) >= 4 and parts[:2] == ('.claude', 'skills'):
            groups.setdefault(parts[2], {})['/'.join(parts[3:])] = entry
    generated = state['generated']
    baseline_names = {Path(p).parts[2] for p in baseline
                      if p.startswith('.claude/skills/') and len(Path(p).parts) >= 4}
    for name in sorted(set(groups) | set(generated) | baseline_names):
        root = Path('.agents/skills') / name
        if name not in groups:
            report['skills_deleted'].append(f'{name} (upstream deleted; Codex retained)')
            continue
        try:
            if name in blocked:
                raise ValueError('source Claude skill has conflicts')
            source = groups[name]
            if 'SKILL.md' not in source:
                raise ValueError('upstream missing SKILL.md')
            desired = {}
            for relative, entry in source.items():
                if entry[0] not in REGULAR or protected(f'.claude/skills/{name}/{relative}', patterns):
                    raise ValueError(f'unsafe/protected skill resource: {relative}')
                data = blob(entry)
                if relative == 'SKILL.md':
                    try:
                        frontmatter(data, name)
                    except (ValueError, yaml.YAMLError) as e:
                        report['conversion_notes'].append(f'{name}: normalized Codex name/description; raw retained: {e}')
                    data = convert(data, name)
                elif relative.endswith('.md'):
                    data = data.decode('utf-8').replace('.claude/skills/', '.agents/skills/').encode()
                desired[relative] = [entry[0], data]
            existing = disk_skill(root)
            owned = generated.get(name)
            if owned is None and root.exists():
                raise ValueError('existing custom Codex skill is not owned by sync')
            if owned is not None and fingerprint(existing) != owned:
                raise ValueError('Codex skill locally modified/added/deleted; entire skill retained')
            # Preserve deleted resources too; do not accidentally remove user work.
            removed = set(existing) - set(desired)
            if removed:
                raise ValueError('upstream removed resources (manual review): ' + ', '.join(sorted(removed)))
            if fingerprint(existing) != fingerprint(desired):
                for relative, (mode, data) in desired.items():
                    safe_path(str(root / relative))
                for relative, (mode, data) in desired.items():
                    write_file(str(root / relative), mode, data)
                report['skills_added' if owned is None else 'skills_modified'].append(name)
            generated[name] = fingerprint(desired)
        except (ValueError, OSError, UnicodeError, yaml.YAMLError) as e:
            report['conflicts'].append(f'{root}: {e}')
    count, errors = validate_all()
    report['validation_errors'] = errors
    state['upstream'] = git('rev-parse', upstream).decode().strip()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    serialized_state = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + '\n'
    if serialized_state == previous_state and not any(report[k] for k in
            ('raw_added', 'raw_modified', 'skills_added', 'skills_modified')) and Path(REPORT).is_file():
        print(Path(REPORT).read_text())
        print('Current validation:', json.dumps(errors, ensure_ascii=True))
        return report
    state_path.write_text(serialized_state)
    sections = ['# Upstream synchronization review', '',
                f"Upstream: `Kouzui-HUANG/biz-ai-video-course@{state['upstream']}`", '',
                'All deletions are retained. Conflicts require manual resolution; no force overwrite.',
                f'Validated skills: {count}; validation errors: {len(errors)}.', '']
    for category, items in report.items():
        sections += ['## ' + category, '']
        # JSON quoting prevents paths from injecting Markdown headings/mentions.
        sections += ['- `' + json.dumps(s, ensure_ascii=True).replace('`', '\\u0060') + '`' for s in items] or ['- None']
        sections += ['']
    content = '\n'.join(sections)
    safe_path(REPORT).write_text(content)
    print(content)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', default='refs/remotes/teacher/main')
    parser.add_argument('--base')
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    if args.validate_only:
        count, errors = validate_all()
        print(json.dumps({'valid_skills': count, 'errors': errors}, ensure_ascii=False, indent=2))
        return bool(errors)
    if git('status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('tracked worktree must be clean')
    base = args.base or git('merge-base', 'HEAD', args.upstream).decode().strip()
    config = json.loads(Path('.safe-sync/config.json').read_text())
    sync(args.upstream, base, config['protected'])
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
