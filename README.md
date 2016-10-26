# HPC Edge Service
**Authors:** J. Taylor Childers (Argonne National Laboratory), Tom Uram (Argonne National Laboratory), Doug Benjamin (Duke University)

An HPC Edge Service to manage remote job submission. The goal of this service is to provide a secure interface for submitting jobs to large computing resources.

# Prerequisites
This Edge Service uses [RabbitMQ](https://www.rabbitmq.com/) to communicate between the outside (Argo) and inside (Balsam) services. This service must be running on an accessible server machine to use this Edge Service.

# Installation
```
git clone git@github.com:hep-cce/hpc-edge-service.git
cd hpc-edge-service
virtualenv argobalsam_env
source argobalsam_env/bin/activate
pip install pip --upgrade
pip install django
pip install pika
pip install future
export ARGOBALSAM_INSTALL_PATH=$PWD
mkdir log argojobs balsamjobs exe
```

# Configure Databases
You can find many settings to change. There are Django specific settings in `argobalsam/settings.py` and Edge Service settings in `user_settings.py`.

To create and initialize the default sqlite3 database without password protections do:
```
./manage.py makemigrations argo
./manage.py makemigrations balsam
./manage.py migrate
./manage -h
```



