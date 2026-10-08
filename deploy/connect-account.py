#!/usr/bin/env python3
"""Interactive TLS client. Passwords are read privately, never shell arguments."""
import getpass
import http.cookiejar
import json
from pathlib import Path
import sys
import urllib.request
import urllib.error
import urllib.parse

def main():
    settings=dict(line.split('=',1) for line in Path('.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
    origin=settings['PUBLIC_URL'].rstrip('/')
    if urllib.parse.urlsplit(origin).scheme!='https': raise ValueError('Verified HTTPS required')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args): return None
    client=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),NoRedirect())
    csrf=''
    def request(path,body=None,method=None):
        payload=json.dumps(body).encode() if body is not None else None
        req=urllib.request.Request(origin+'/api'+path,data=payload,method=method,headers={'Content-Type':'application/json','Origin':origin,'X-CSRF-Token':csrf})
        with client.open(req,timeout=10) as response: return json.load(response)
    print('MT5 account connection |',origin)
    print('Use your panel account. MT5 passwords are encrypted on the server.')
    logged=request('/login',{'email':input('Panel email: ').strip(),'password':getpass.getpass('Panel password: ')})
    csrf=logged['csrf']
    try:
        accounts=request('/accounts')
        for i,a in enumerate(accounts,1): print(f"{i}) {a['name']} | {a['login']} | {a['server']}")
        selection=input('Select account number, or 0 to create: ').strip()
        if selection=='0':
            data={'name':input('Account name: ').strip(),'login':input('MT5 login number: ').strip(),'server':input('Exact MT5 broker server name: ').strip(),'initial_equity':float(input('Initial equity in account currency: ')),'password':getpass.getpass('MT5 trading password: ')}
            if not data['password']: raise ValueError('MT5 password is required for password connector')
            result=request('/accounts',data); aid=result['id']
        else:
            index=int(selection)-1
            if index<0 or index>=len(accounts): raise ValueError('Invalid account number')
            a=accounts[index]; aid=a['id']
            if a['config']['running']: raise ValueError('Stop the agent in the panel before changing connection credentials')
            result=request('/accounts/'+aid+'/connection',{'password':getpass.getpass('MT5 trading password: ')},'PUT')
        print('\nAccount ID:',aid)
        print('New bridge key (save privately, shown only once):',result['bridge_key'])
        print('Download the connector from the panel: Account connection > Download connector.')
        print('Windows: unzip, run setup.ps1 with Python 3.12 x64 and an installed broker MT5 terminal.')
        print('Linux: use Windows Python inside an already prepared isolated Wine prefix; see mt5/README.md.')
        print('Credentials saved. Broker login is not confirmed until the connector reports it.')
    finally:
        try: request('/logout',{})
        except Exception: pass

if __name__=='__main__':
    try: main()
    except urllib.error.HTTPError as error:
        print('Panel HTTP',error.code,'; check login, stopped agent and account settings.',file=sys.stderr)
        raise SystemExit(1)
    except Exception:
        print('Connection setup failed. Check verified HTTPS and entered values. No passwords were logged.',file=sys.stderr)
        raise SystemExit(1)
