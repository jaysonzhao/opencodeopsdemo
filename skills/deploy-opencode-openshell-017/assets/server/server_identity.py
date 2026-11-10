"""Return OpenCode process identity without exposing its environment or command output."""
import json
from pathlib import Path


def identities():
    found=[]
    pid_namespace=str(Path('/proc/self/ns/pid').readlink())
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():continue
        try:
            if str((path/'exe').resolve())!='/opt/opencode/bin/opencode':continue
            args=(path/'cmdline').read_bytes().split(b'\0')
            if b'serve' not in args or b'--port' not in args:continue
            if args[args.index(b'--port')+1]!=b'4096':continue
            # Field 22 is starttime; fields after the closing comm parenthesis start at 3.
            ticks=(path/'stat').read_text().rsplit(')',1)[1].split()[19]
            found.append({'pid':int(path.name),'start_ticks':ticks,'pid_namespace':pid_namespace})
        except (OSError,ValueError,IndexError):continue
    if len(found)>1:raise RuntimeError('Multiple OpenCode servers on port 4096; resolve ownership')
    return found[0] if found else None


if __name__=='__main__':print(json.dumps({'server':identities()}))
