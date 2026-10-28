"""Read-only 40-second cgroup/VPS sampling during an authorized native-agent test."""
from pathlib import Path
import datetime,json,subprocess,time
container=json.loads(subprocess.check_output(['docker','inspect','hermes-agent']))[0]
pid=container['State']['Pid']
relative=next(l.split('::',1)[1] for l in Path('/proc/'+str(pid)+'/cgroup').read_text().splitlines() if l.startswith('0::'))
cgroup=Path('/sys/fs/cgroup')/relative.lstrip('/')
def cpu_usec():
    return int(next(l.split()[1] for l in (cgroup/'cpu.stat').read_text().splitlines() if l.startswith('usage_usec ')))
started=time.monotonic()
previous=started
cpu=cpu_usec()
samples=[]
while time.monotonic()-started<40:
    time.sleep(1)
    now=time.monotonic()
    current=cpu_usec()
    mem={l.split(':')[0]:int(l.split()[1]) for l in Path('/proc/meminfo').read_text().splitlines()}
    samples.append({'second':round(now-started,1),'hermes_cpu_percent':round((current-cpu)/(now-previous)/10000,1),
       'hermes_memory_mib':round(int((cgroup/'memory.current').read_text())/1048576,1),
       'vps_available_mib':round(mem['MemAvailable']/1024,1)})
    previous,cpu=now,current
result={'checked_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
 'duration_seconds':round(time.monotonic()-started,1),'sample_count':len(samples),
 'max_hermes_cpu_percent':max(s['hermes_cpu_percent'] for s in samples),
 'max_hermes_memory_mib':max(s['hermes_memory_mib'] for s in samples),
 'min_vps_available_mib':min(s['vps_available_mib'] for s in samples),'samples':samples}
print(json.dumps(result,indent=2))
