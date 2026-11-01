import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from avecove_namer import __version__
from avecove_namer.webapp import App, handler_factory


class ReleaseTests(unittest.TestCase):
    def test_health_reports_version_revision_and_running_jobs_without_secrets(self):
        app = App.__new__(App)
        app._jobs_lock = threading.Lock()
        app._jobs = {'a':{'status':'running'}, 'b':{'status':'completed'}}
        server = ThreadingHTTPServer(('127.0.0.1',0), handler_factory(app))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/health') as response:
                data = json.load(response)
            self.assertEqual(data['version'], __version__)
            self.assertEqual(data['service'], 'AveCove Namer')
            self.assertEqual(data['activeJobs'], 1)
            self.assertEqual(set(data), {'status','service','version','revision','activeJobs'})
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_publisher_remote_script_compiles_and_has_deploy_guards(self):
        path = Path(__file__).parents[1] / 'scripts/publish.py'
        spec = importlib.util.spec_from_file_location('publisher', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        compile(module.REMOTE, 'remote-publisher', 'exec')
        for guard in ("health['activeJobs'] == 0", 'current.is_symlink()', 'not target.exists()', 'link.symlink_to(old)'):
            self.assertIn(guard, module.REMOTE)

    def test_package_version_is_consistent(self):
        root = Path(__file__).parents[1]
        self.assertIn(f'version = "{__version__}"', (root / 'pyproject.toml').read_text())
