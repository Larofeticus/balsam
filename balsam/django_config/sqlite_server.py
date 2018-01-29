from io import StringIO
from traceback import print_exc
import json
import os
import logging
import time
import zmq
import signal

os.environ['IS_BALSAM_SERVER']="True"
os.environ['IS_SERVER_DAEMON']="False"
os.environ['DJANGO_SETTINGS_MODULE'] = 'balsam.django_config.settings'

import django
django.setup()

from balsam.service.models import BalsamJob
from balsam.django_config import serverinfo

logger = logging.getLogger('balsam.django_config.sqlite_server')

SERVER_PERIOD = 1000
TERM_LINGER = 3 # wait 3 sec after final save() to exit
terminate = False


class ZMQServer:
    def __init__(self, db_path):
        # connect to local sqlite DB thru ORM
        self.BalsamJob = BalsamJob

        self.info = serverinfo.ServerInfo(db_path)
        self.address = self.info['address']
        port = int(self.address.split(':')[2])

        self.context = zmq.Context(1)
        self.socket = self.context.socket(zmq.REP)
        self.socket.bind(f'tcp://*:{port}')
        logger.info(f"db_writer bound to socket @ {self.address}")

    def recv_request(self):
        events = self.socket.poll(timeout=SERVER_PERIOD)
        if events:
            message = self.socket.recv().decode('utf-8')
            logger.debug(f'request: {message}')
        else:
            message = None
        return message

    def send_reply(self, msg):
        self.socket.send_string(msg)
        logger.debug(f"Sent reply {msg}")

    def save(self, job_msg):
        d = json.loads(job_msg)
        job = self.BalsamJob.from_dict(d)
        force_insert = d['force_insert']
        force_update = d['force_update']
        using = d['using']
        update_fields = d['update_fields']
        job.save(force_insert, force_update, using, update_fields)
        logger.info(f"db_writer Saved {job.cute_id}")
        return time.time()


def server_main(db_path):
    logger.debug("hello from server_main")
    parent_pid = os.getppid()
    global terminate

    def handler(signum, stack):
        global terminate
        terminate = True
        logger.debug("Got sigterm; will shut down soon")

    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)

    server = ZMQServer(db_path)
    last_save_time = time.time()

    while not terminate or time.time() - last_save_time < TERM_LINGER:
        message = server.recv_request()
        if terminate:
            logger.debug(f"shut down in {TERM_LINGER - (time.time()-last_save_time)} seconds")

        if message is None:
            if os.getppid() != parent_pid:
                logger.info("detected parent died; server quitting soon")
                terminate = True
        elif 'job_id' in message:
            logger.debug("sending ACK_SAVE")
            last_save_time = server.save(message)
            server.send_reply("ACK_SAVE")
        else:
            logger.debug("sending ACK")
            server.send_reply("ACK")

if __name__ == "__main__":
    db_path = os.environ['BALSAM_DB_PATH']
    try:
        server_main(db_path)
    except:
        buf = StringIO()
        print_exc(file=buf)
        logger.exception(f"db_writer Uncaught exception:\n%s", buf.getvalue())
    finally:
        logger.info("exiting server main")
