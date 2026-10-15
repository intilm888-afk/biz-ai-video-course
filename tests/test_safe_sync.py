"""Integration tests use disposable Git repositories and real three-way history."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'scripts/safe_sync/sync.py'
spec = importlib.util.spec_from_file_location('safe_sync', MODULE)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.previous = Path.cwd()
        self.temp = tempfile.TemporaryDirectory()
        os.chdir(self.temp.name)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.write('README.md', 'base\n')
        self.write('.claude/skills/demo/SKILL.md', '---\nname: demo\ndescription: Example\n---\nUse .claude/skills/demo/ref.md\n')
        self.write('.claude/skills/demo/ref.md', 'original\n')
        self.commit()
        self.base = self.git('rev-parse', 'HEAD')
        self.git('branch', 'teacher')

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def git(self, *args):
        return subprocess.check_output(['git', *args], stderr=subprocess.PIPE).decode().strip()

    def write(self, path, text):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self):
        self.git('add', '-A')
        self.git('commit', '-qm', 'fixture')

    def run_sync(self):
        return sync.sync('teacher', self.base)

    def teacher(self, changes):
        self.git('switch', '-q', 'teacher')
        for path, value in changes.items():
            if value is None:
                Path(path).unlink()
            else:
                self.write(path, value)
        self.commit()
        self.git('switch', '-q', 'main')

    def test_initialization_and_idempotence(self):
        report = self.run_sync()
        self.assertEqual(report['skills_added'], ['demo'])
        self.assertIn('.agents/skills/demo/ref.md', Path('.agents/skills/demo/SKILL.md').read_text())
        self.assertFalse(sync.validate_all()[1])
        self.commit()
        self.run_sync()
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_custom_codex_directory_is_never_adopted(self):
        self.write('.agents/skills/demo/custom.md', 'mine')
        report = self.run_sync()
        self.assertFalse(Path('.agents/skills/demo/SKILL.md').exists())
        self.assertTrue(any('not owned' in x for x in report['conflicts']))

    def test_raw_local_changes_and_independent_updates(self):
        self.write('README.md', 'my work\n')
        self.commit()
        self.teacher({'README.md': 'teacher work\n', 'lesson.md': 'new lesson\n'})
        report = self.run_sync()
        self.assertEqual(Path('README.md').read_text(), 'my work\n')
        self.assertEqual(Path('lesson.md').read_text(), 'new lesson\n')
        self.assertTrue(any('README' in x for x in report['conflicts']))

    def test_entire_codex_skill_stops_on_local_addition(self):
        self.run_sync()
        self.write('.agents/skills/demo/custom.md', 'my work')
        self.commit()
        self.teacher({'.claude/skills/demo/ref.md': 'teacher revision\n'})
        report = self.run_sync()
        self.assertEqual(Path('.agents/skills/demo/ref.md').read_text(), 'original\n')
        self.assertEqual(Path('.agents/skills/demo/custom.md').read_text(), 'my work')
        self.assertTrue(report['conflicts'])

    def test_entire_claude_skill_stops_on_local_change(self):
        self.write('.claude/skills/demo/SKILL.md', 'my custom source')
        self.commit()
        self.teacher({'.claude/skills/demo/ref.md': 'teacher revision\n'})
        report = self.run_sync()
        self.assertEqual(Path('.claude/skills/demo/ref.md').read_text(), 'original\n')
        self.assertTrue(report['conflicts'])

    def test_deletions_preserve_raw_and_codex_skills(self):
        self.run_sync()
        self.commit()
        self.teacher({'.claude/skills/demo/SKILL.md': None, '.claude/skills/demo/ref.md': None})
        report = self.run_sync()
        self.assertTrue(Path('.claude/skills/demo/SKILL.md').exists())
        self.assertTrue(Path('.agents/skills/demo/SKILL.md').exists())
        self.assertTrue(report['skills_deleted'])
        self.assertTrue(report['raw_deleted'])

    def test_protected_automation_and_configs(self):
        self.teacher({'.github/workflows/evil.yml': 'bad', '.claude/settings.json': 'bad',
                      'AGENTS.md': 'bad', '.safe-sync/config.json': 'bad'})
        report = self.run_sync()
        self.assertEqual(len(report['protected']), 4)
        self.assertFalse(Path('.github/workflows/evil.yml').exists())
        self.assertFalse(Path('AGENTS.md').exists())

    def test_symlinks_and_untracked_files(self):
        self.teacher({'linked/file.md': 'evil', 'lesson.md': 'teacher'})
        outside = Path(self.temp.name).parent / ('sync-target-' + Path(self.temp.name).name)
        outside.mkdir()
        try:
            Path('linked').symlink_to(outside, target_is_directory=True)
            self.write('lesson.md', 'my untracked work')
            report = self.run_sync()
            self.assertFalse((outside / 'file.md').exists())
            self.assertEqual(Path('lesson.md').read_text(), 'my untracked work')
            self.assertGreaterEqual(len(report['conflicts']), 2)
        finally:
            outside.rmdir()

    def test_safe_conversion_of_single_line_description_and_name(self):
        data = b'---\nname: Demo Name\ndescription: Use this: carefully\n---\nBody\n'
        result = sync.convert(data, 'demo')
        meta, _ = sync.frontmatter(result, 'demo')
        self.assertEqual(meta['description'], 'Use this: carefully')
        self.assertEqual(meta['name'], 'demo')
        with self.assertRaises(ValueError):
            sync.convert(b'---\nname: demo\nname: other\ndescription: x\n---\n', 'demo')

    def test_missing_skill_yaml_is_reported_and_not_converted(self):
        self.teacher({'.claude/skills/broken/ref.md': 'no SKILL.md'})
        report = self.run_sync()
        self.assertTrue(any('missing SKILL.md' in s for s in report['conflicts']))
        self.assertFalse(Path('.agents/skills/broken').exists())

    def test_clean_owned_skill_updates(self):
        self.run_sync()
        self.commit()
        self.teacher({'.claude/skills/demo/ref.md': 'revision\n'})
        report = self.run_sync()
        self.assertEqual(report['skills_modified'], ['demo'])
        self.assertEqual(Path('.agents/skills/demo/ref.md').read_text(), 'revision\n')

    def test_publisher_preserves_main_and_reuses_pr_without_extra_commit(self):
        pub_spec = importlib.util.spec_from_file_location('publisher', MODULE.with_name('run.py'))
        pub = importlib.util.module_from_spec(pub_spec)
        pub_spec.loader.exec_module(pub)
        self.write('.safe-sync/config.json', '{"protected": []}')
        self.commit()
        main_before = self.git('rev-parse', 'main')
        with tempfile.TemporaryDirectory() as remote:
            subprocess.run(['git', 'clone', '-q', '--bare', '.', remote], check=True)
            self.git('remote', 'add', 'origin', remote)
            self.git('fetch', '-q', 'origin')
            actual_run, actual_git = pub.run, pub.git
            calls = []
            active_pr = []

            def fake_run(*args, **kwargs):
                if args[0] == 'gh':
                    calls.append(args)
                    if args[1:3] == ('pr', 'list'):
                        return json.dumps(active_pr)
                    return ''
                return actual_run(*args, **kwargs)

            def redirected_git(*args, **kwargs):
                teacher_url = 'https://github.com/Kouzui-HUANG/biz-ai-video-course.git'
                if teacher_url in args:
                    args = tuple(remote if a == teacher_url else a for a in args)
                return actual_git(*args, **kwargs)

            environment = {'GITHUB_REPOSITORY': pub.REPO, 'GITHUB_REF': 'refs/heads/main',
                           'GH_TOKEN': 'unused-test-credential'}
            with patch.dict(os.environ, environment), patch.object(pub, 'run', fake_run), \
                    patch.object(pub, 'git', redirected_git):
                pub.main()
                head = self.git('rev-parse', 'HEAD')
                self.assertEqual(self.git('rev-parse', 'origin/main'), main_before)
                self.assertTrue(any(c[1:3] == ('pr', 'create') for c in calls))
                self.assertTrue(Path('.agents/skills/demo/SKILL.md').exists())
                self.git('switch', '-q', '--detach', 'main')
                self.git('branch', '-m', pub.BRANCH, 'previous-test-sync')
                active_pr.append({'number': 1, 'body': pub.MARKER,
                                  'author': {'login': 'app/github-actions'}})
                pub.main()
                self.assertEqual(self.git('rev-parse', 'HEAD'), head)
                self.assertEqual(self.git('rev-parse', 'origin/main'), main_before)
                self.assertTrue(any(c[1:3] == ('pr', 'edit') for c in calls))
                self.assertEqual(sum(c[1:3] == ('pr', 'create') for c in calls), 1)

    def test_codex_resource_deletion_stops_whole_skill(self):
        self.run_sync()
        self.commit()
        self.teacher({'.claude/skills/demo/ref.md': None})
        report = self.run_sync()
        self.assertTrue(Path('.agents/skills/demo/ref.md').exists())
        self.assertTrue(any('removed resources' in s for s in report['conflicts']))


if __name__ == '__main__':
    unittest.main()
