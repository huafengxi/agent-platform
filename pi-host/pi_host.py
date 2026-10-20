#!/usr/bin/env python3
"""Independent ordinary pi host. Launch with JSON on stdin (argument '-').

A caller may also supply an explicit JSON input file. Only host.json, the socket
and optional diagnostics are runtime output; there is no launch-intent marker.
"""
import json
import os
from pathlib import Path
import sys
import threading

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from transport import LaunchConfig, Transport
import host_io
import local_process


def load_config(path):
    obj = json.load(sys.stdin) if path == '-' else json.loads(Path(path).read_text())
    required = {'sessionFile', 'cwd', 'sessionName', 'piBinary', 'socketPath', 'env', 'extensionArgv'}
    if type(obj) is not dict or set(obj) != required:
        raise ValueError('declaration must contain exactly ' + ', '.join(sorted(required)))
    if not isinstance(obj['extensionArgv'], list) or any(not isinstance(x, str) for x in obj['extensionArgv']):
        raise ValueError('extensionArgv must be an argv list')
    env = dict(obj['env'])
    if 'RUND_LEASE_FD' in env:
        raise ValueError('RUND_LEASE_FD is runtime-owned, not configurable')
    if 'RUND_LEASE_FD' in os.environ:
        env['RUND_LEASE_FD'] = os.environ['RUND_LEASE_FD']
    return LaunchConfig(obj['sessionFile'], obj['cwd'], obj['sessionName'], obj['piBinary'],
                        tuple(obj['extensionArgv']), obj['socketPath'], env)


def fact(event, **values):
    # Optional diagnostics for standalone callers. Runtime readers use host.json,
    # never parse this log as a second source of process truth.
    print(json.dumps(dict(event=event, at=host_io.now_ts(), **values)), flush=True)


class Host(Transport):
    def __init__(self, config):
        self.config = config
        self.session_file = config.session_file
        self.session_name = config.session_name
        self.sock_path = config.socket_path
        self.state_file = str(Path(self.sock_path).with_name('host.json'))
        self.stderr_log = str(Path(self.sock_path).with_name('host.log'))
        self.binding = dict(session=os.path.realpath(self.session_file),
                            name=self.session_name, cwd=os.path.realpath(config.cwd))
        self.record = None
        self.init_transport()

    def read_socket_owner(self):
        previous = host_io.read_owner(self.state_file)
        if previous is None:
            return None
        if not isinstance(previous, dict) or previous.get('binding') != self.binding:
            raise RuntimeError('host state binding mismatch')
        owner = previous['host']
        return dict(pid=owner['pid'], procStart=owner['start'])

    def publish_socket_owner(self, owner):
        # Called only AFTER bind, by this actual P. No caller launch prediction.
        self.record = dict(binding=self.binding, host=local_process.identity(os.getpid()),
                           child=None, childWait=None)
        self.publish()

    def remove_socket_owner(self):
        # Keep actual identities/wait available after endpoint cleanup.
        pass

    def publish(self):
        host_io.write_owner(self.state_file, self.record)

    def run(self):
        self.setup_socket()
        try:
            self.launch(self.config)
            self.record['child'] = local_process.identity(self.pi.pid)
            if self.record['child'] is None:
                raise RuntimeError('child start identity unavailable')
            self.publish()
            threading.Thread(target=self.accept_loop, daemon=True, name='host-accept').start()
            fact('host_started', hostPid=os.getpid(), hostProcStart=self.record['host']['start'],
                 childPid=self.pi.pid, childProcStart=self.record['child']['start'],
                 sessionFile=self.session_file, sessionName=self.session_name, socketPath=self.sock_path)
            rc = self.pi.wait()  # sole pi wait, no restart or report predicate
            self.record['childWait'] = dict(returncode=rc)
            self.publish()
            self.finish_pumps()
            fact('child_wait', childPid=self.pi.pid, returncode=rc)
            return rc if rc >= 0 else 128 - rc
        except Exception as e:
            self.record['error'] = repr(e)
            self.publish()
            raise
        finally:
            self.cleanup_socket()


def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError(__doc__)
        return Host(load_config(sys.argv[1])).run()
    except Exception as e:
        fact('host_error', error=repr(e))
        return 1


if __name__ == '__main__':
    sys.exit(main())
