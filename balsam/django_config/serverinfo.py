import json
import os
import socket
from getpass import getuser

ADDRESS_FNAME = 'server-info'

class ServerInfo:
    def __init__(self, balsam_db_path):
        balsam_db_path = os.path.abspath(os.path.expanduser(balsam_db_path))
        self.path = os.path.join(balsam_db_path, ADDRESS_FNAME)
        self.data = {'balsamdb_path' : balsam_db_path}

        if not os.path.exists(self.path):
            self.update(self.data)
        else:
            self.refresh()

    def get_free_port_and_address(self):
        hostname = socket.gethostname()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = int(sock.getsockname()[1])
        sock.close()

        address = f'tcp://{hostname}:{port}'
        return address

    def get_free_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('', 0))
        port = int(sock.getsockname()[1])
        sock.close()
        return port

    def get_postgres_info(self):
        hostname = socket.gethostname()
        port = self.get_free_port()
        pg_db_path = os.path.join(self['balsamdb_path'], 'balsamdb')
        info = dict(host=hostname, port=port, pg_db_path=pg_db_path)
        return info

    def update_postgres_config(self):
        conf_path = os.path.join(self['pg_db_path'], 'postgresql.conf')
        config = open(conf_path).read()

        with open(f"{conf_path}.new", 'w') as fp:
            for line in config.split('\n'):
                if line.startswith('port'):
                    port_line = f"port={self['port']} # auto-set by balsam db\n"
                    fp.write(port_line)
                else:
                    fp.write(line + "\n")
        os.rename(f"{conf_path}.new", conf_path)

    def reset_server_address(self):
        info = self.get_postgres_info()
        self.update(info)
        self.update_postgres_config()
    
    def update(self, update_dict):
        if os.path.exists(self.path): self.refresh()
        self.data.update(update_dict)
        with open(self.path, 'w') as fp:
            fp.write(json.dumps(self.data))

    def get(self, key, default=None):
        if key in self.data:
            return self.data[key]
        else:
            return default

    def refresh(self):
        with open(self.path, 'r') as fp:
            file_data = json.loads(fp.read())
        self.data.update(file_data)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.update({key:value})

    def django_db_config(self):
        ENGINE = 'django.db.backends.postgresql_psycopg2'
        NAME = 'balsam'
        OPTIONS = {'connect_timeout' : 5}

        user = getuser()
        password = self.get('password', '')
        host = self.get('host', '')
        port = self.get('port', '')


        db = dict(ENGINE=ENGINE, NAME=NAME,
                  OPTIONS=OPTIONS, USER=user, PASSWORD=password,
                  HOST=host, PORT=port, CONN_MAX_AGE=60)

        DATABASES = {'default':db}
        return DATABASES
