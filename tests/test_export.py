import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('export_static',Path(__file__).resolve().parents[1]/'export_static.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

class PublicationTests(unittest.TestCase):
    def test_never_publish_account_or_csrf_or_host_path(self):
        data={'meta':{'now':'x','csrf_token':'secret','project_path':'private','config':{'token':'private'}},
              'index':None,'stocks':[],'paper':{'positions':[{'symbol':'private'}],'cash':123},
              'runs':[{'target_date':'2026-10-16','error':'private path','ok':1}]}
        result=module.public_dashboard(data)
        self.assertEqual(set(result),{'meta','index','stocks','runs'})
        serialized=str(result)
        self.assertNotIn('private',serialized)
        self.assertNotIn('secret',serialized)
        self.assertEqual(data['meta']['csrf_token'],'secret')

if __name__=='__main__':unittest.main()
