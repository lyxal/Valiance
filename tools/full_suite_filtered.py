#!/usr/bin/env python3
"""Run every Valiance core/stdlib unittest module except terminal/Textual modules.

Isolate batches in killable process groups so a test cannot hang the entire run.
"""
from __future__ import annotations
import argparse, concurrent.futures, json, os, pathlib, re, signal, subprocess, sys, time

from tools.test_suite import discover_test_files, partition_test_files, _worker_command

p=argparse.ArgumentParser()
p.add_argument('--suite', choices=('language-core','standard-library'), required=True)
p.add_argument('--workers', type=int, default=3)
p.add_argument('--timeout', type=int, default=420)
p.add_argument('--output', type=pathlib.Path, required=True)
a=p.parse_args()
files=discover_test_files(pathlib.Path('tests'),('test_*.py',),a.suite)
excluded=[str(f) for f in files if 'terminal' in f.stem.lower() or 'textual' in f.stem.lower()]
files=tuple(f for f in files if str(f) not in excluded)
batches=partition_test_files(files,a.workers*4)
print(f'{a.suite}: {len(files)} included modules, {len(excluded)} excluded modules {excluded}, {len(batches)} batches, {a.workers} workers',flush=True)
env=os.environ.copy();root=pathlib.Path.cwd();env['PYTHONPATH']=os.pathsep.join([str(root/'src'),str(root/'tests'),str(root),env.get('PYTHONPATH','')]);env['VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL']='1'
started=time.monotonic()
a.output.parent.mkdir(parents=True,exist_ok=True)

def go(item):
 idx,b=item;command=_worker_command(b.targets,verbose=False)
 t0=time.monotonic()
 proc=subprocess.Popen(
    command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    encoding='utf8', errors='replace', env=env,
    start_new_session=(os.name != 'nt'),
    creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0),
 )
 try:
  out,_=proc.communicate(timeout=a.timeout);rc=proc.returncode;timedout=False
 except subprocess.TimeoutExpired:
  timedout=True
  if os.name == 'nt':
   subprocess.run(['taskkill','/T','/F','/PID',str(proc.pid)],capture_output=True)
  else:
   try:os.killpg(proc.pid,signal.SIGTERM)
   except ProcessLookupError:pass
  try:out,_=proc.communicate(timeout=4)
  except subprocess.TimeoutExpired:
   if os.name == 'nt':
    proc.kill()
   else:
    try:os.killpg(proc.pid,signal.SIGKILL)
    except ProcessLookupError:pass
   out,_=proc.communicate(timeout=4)
  rc=124
 elapsed=round(time.monotonic()-t0,2)
 matches=re.findall(r'Ran (\d+) tests? in ([0-9.]+)s',out)
 number=sum(int(n) for n,_ in matches)
 return {'batch':idx,'targets':[t.argument() for t in b.targets],'modules':len(set(b.files)),'tests':number,'duration_s':elapsed,'status':'timeout' if timedout else 'passed' if rc==0 else 'failed','returncode':rc,'output':out}

results=[]
with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:
 for res in concurrent.futures.as_completed([pool.submit(go,(i,b)) for i,b in enumerate(batches,1)]):
  r=res.result();results.append(r)
  print(f"{a.suite} [{len(results):02d}/{len(batches):02d}] batch {r['batch']:02d} {r['status']} {r['tests']} tests in {r['duration_s']}s",flush=True)
  if r['status']!='passed':print('FAILED/TIMED OUT:',r['targets'],'\n',r['output'][-6000:],flush=True)
  report={'suite':a.suite,'environment_python':sys.version,'excluded':excluded,'modules':len(files),'elapsed_s':round(time.monotonic()-started,2),'tests_completed':sum(x['tests'] for x in results),'batches_completed':len(results),'batches_total':len(batches),'passed_batches':sum(x['status']=='passed' for x in results),'failed_batches':sum(x['status']=='failed' for x in results),'timed_out_batches':sum(x['status']=='timeout' for x in results),'results':sorted(results,key=lambda x:x['batch'])}
  a.output.write_text(json.dumps(report,indent=2)+'\n')
print(f"FINAL {a.suite}: {report['tests_completed']} tests, {report['passed_batches']}/{len(batches)} passed batches; failed={report['failed_batches']} timeout={report['timed_out_batches']} elapsed={report['elapsed_s']}s",flush=True)
sys.exit(0 if report['passed_batches']==len(batches) else 1)
