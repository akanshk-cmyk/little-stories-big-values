"""Explicit loopback live Ramayan text-only test; uses the existing account store."""
import argparse
from backend.env_config import load_local_env


def main():
    load_local_env()
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8003);parser.add_argument('--check',action='store_true',help='Check readiness without model calls or starting the server.');args=parser.parse_args()
    import json
    from backend.readiness import check_local_readiness
    readiness=check_local_readiness()
    if args.check:
        print(json.dumps(readiness,indent=2))
        if readiness['status']!='READY':raise SystemExit(2)
        return
    if readiness['status']!='READY':
        for check in readiness['checks']:
            if check['status']=='BLOCKED':print(check['message'])
        raise SystemExit('Story service is not ready. Run with --check for details. No paid source review was started.')
    from backend.local_ramayan_test import build_app
    from backend.server import Handler,ThreadingHTTPServer
    api,router=build_app(interactive=True)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler);server.account_api=api
    print('Real local-PDF/Groq text-only Ramayan test: http://localhost:'+str(args.port)+'/index.html',flush=True)
    try:server.serve_forever()
    finally:
        server.server_close();api.workflow.close();router.close()


if __name__=='__main__':main()
