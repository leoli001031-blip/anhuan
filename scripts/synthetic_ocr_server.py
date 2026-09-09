"""Test-only TLS OCR endpoint; accepts only the exact rendered synthetic PDF/JPEG images."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import hashlib
import json
import ssl
ROOT=Path('/synthetic')
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_POST(self):
        try:
            assert self.path=='/chat/completions'
            assert self.headers.get('Authorization')=='Bearer '+Path('/synthetic-auth/ocr_key').read_text().strip()
            n=int(self.headers.get('Content-Length','0'));assert 0<n<9*1024*1024
            data=json.loads(self.rfile.read(n));assert data['model']=='synthetic-hash-bound'
            images=[x['image_url']['url'] for m in data['messages'] if isinstance(m.get('content'),list) for x in m['content'] if x.get('type')=='image_url']
            assert len(images)==1 and images[0].startswith('data:image/jpeg;base64,')
            raw=base64.b64decode(images[0].split(',',1)[1],validate=True)
            match=json.loads((ROOT/'ocr_responses.json').read_text())[hashlib.sha256(raw).hexdigest()]
            body=json.dumps({'choices':[{'message':{'content':match['text']},'finish_reason':'stop'}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            print(json.dumps({'event':'synthetic_ocr_response','format':match['format'],'image_sha256':hashlib.sha256(raw).hexdigest()}),flush=True)
        except Exception:
            self.send_response(422);self.send_header('Content-Length','0');self.end_headers()
server=ThreadingHTTPServer(('0.0.0.0',8443),Handler)
ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(ROOT/'tls.pem',ROOT/'tls.key')
server.socket=ctx.wrap_socket(server.socket,server_side=True);server.serve_forever()
