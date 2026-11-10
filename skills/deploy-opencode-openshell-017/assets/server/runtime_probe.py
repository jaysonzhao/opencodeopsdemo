"""Bounded loopback accept/getpeername probe. Run through OpenShell supervised exec."""
import json
import platform
import socket
import sys


def probe():
    result={'kernel':platform.release(),'scope':'loopback socket behavior only',
            'accept_with_peer':False,'getpeername':False,'bidirectional_io':False,
            'full_runtime_qualification':False}
    stage='socket'
    try:
        with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as listener:
            listener.settimeout(5)
            stage='bind_listen'
            listener.bind(('127.0.0.1',0));listener.listen(1)
            with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as client:
                client.settimeout(5)
                stage='connect';client.connect(listener.getsockname())
                stage='accept_with_peer'
                connection,peer=listener.accept()
                with connection:
                    connection.settimeout(5)
                    if peer[0]!='127.0.0.1':raise RuntimeError('Unexpected peer address')
                    result['accept_with_peer']=True
                    stage='getpeername'
                    if connection.getpeername()!=peer:raise RuntimeError('Peer address mismatch')
                    if client.getpeername()!=listener.getsockname():raise RuntimeError('Listener address mismatch')
                    result['getpeername']=True
                    stage='bidirectional_io'
                    client.sendall(b'x')
                    if connection.recv(1)!=b'x':raise RuntimeError('Request mismatch')
                    connection.sendall(b'y')
                    if client.recv(1)!=b'y':raise RuntimeError('Reply mismatch')
                    result['bidirectional_io']=True
        result['socket_test']='passed'
        return result,0
    except (OSError,RuntimeError) as exc:
        result.update(socket_test='failed',failed_stage=stage,error_type=type(exc).__name__,
                      errno=getattr(exc,'errno',None))
        return result,1


if __name__=='__main__':
    result,status=probe();print(json.dumps(result));sys.exit(status)
