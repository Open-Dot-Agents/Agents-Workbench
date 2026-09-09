"""Check the extended test peer and evidence classification without a model."""
import json
import os
import ssl
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'conformance'))
import extended_server
import run_extended


class ExtendedNativeTests(unittest.TestCase):
    def test_tls_peer_has_leaf_certificate_and_checks_header(self):
        with tempfile.TemporaryDirectory() as directory:
            case=SimpleNamespace(directory=Path(directory),log=Path(directory)/'requests.jsonl',env={})
            with run_extended.remote_peer(case,True) as (url,token):
                context=ssl.create_default_context(cafile=case.env['SSL_CERT_FILE'])
                data=json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}}).encode()
                request=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request,context=context)
                self.assertEqual(error.exception.code,401)
                error.exception.close()
                request.add_header('Authorization',token)
                with urllib.request.urlopen(request,context=context) as response:
                    result=json.load(response)
                self.assertEqual(result['result']['protocolVersion'],'2025-06-18')
                certificate=subprocess.check_output(['openssl','x509','-in',str(case.directory/'cert.pem'),'-noout','-ext','basicConstraints'],text=True)
                self.assertIn('CA:FALSE',certificate)
                observed=run_extended.events(case.log)
                self.assertEqual([e['authorized'] for e in observed],[False,True])
                self.assertNotIn(token,case.log.read_text())

    def test_stdio_peer_records_actual_environment_and_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'log.jsonl'
            request={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'record','arguments':{'marker':'test'}}}
            args=['two words','$(never-run)','semi;colon']
            result=subprocess.run([sys.executable,str(ROOT/'conformance/extended_server.py'),str(log),'expected',*args],
                                  input=json.dumps(request)+'\n',env={**os.environ,'ODA_NATIVE_ENV':'expected'},text=True,capture_output=True,check=True)
            self.assertEqual(json.loads(result.stdout)['result']['content'][0]['text'],'recorded')
            observed=run_extended.events(log)
            self.assertEqual(observed[0]['argv'],args)
            self.assertTrue(observed[1]['environmentMatches'])

    def test_peer_does_not_confuse_failed_request_with_tool_call(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'log.jsonl'
            response=extended_server.respond({'id':1,'method':'tools/call','params':{'name':'unknown','arguments':{'marker':'test'}}},log)
            self.assertTrue(response['result']['isError'])
            self.assertFalse(log.exists())
            self.assertIsNone(extended_server.respond({'method':'notifications/initialized'},log))
