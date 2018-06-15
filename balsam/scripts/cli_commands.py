import os
import subprocess
import sys

from django.conf import settings
from balsam.service import models
from balsam.launcher import dag
import balsam.scripts.ls_commands as lscmd

Job = models.BalsamJob
AppDef = models.ApplicationDefinition

def cmd_confirmation(message=''):
    confirm = ''
    while not confirm.lower() in ['y', 'n']:
        try:
            confirm = input(f"{message} [y/n]: ")
        except: pass
    return confirm.lower() == 'y'

def newapp(args):
    if AppDef.objects.filter(name=args.name).exists():
        raise RuntimeError(f"An application named {args.name} exists")
    
    for arg in (args.executable,args.preprocess,args.postprocess):
        paths = arg.split()
        if arg and not all(os.path.exists(p) for p in paths):
            raise RuntimeError(f"{path} not found")

    app = AppDef()
    app.name = args.name
    app.description = ' '.join(args.description)
    app.executable = args.executable
    app.default_preprocess = args.preprocess
    app.default_postprocess = args.postprocess
    app.environ_vars = ":".join(args.env)
    app.save()
    print(app)
    print("Added app to database")


def newjob(args):
    if not AppDef.objects.filter(name=args.application).exists():
        raise RuntimeError(f"App {args.application} not registered in local DB")

    job = Job()
    job.name = args.name
    job.description = ' '.join(args.description)
    job.workflow = args.workflow
    job.allowed_work_sites = ' '.join(args.allowed_site)

    job.wall_time_minutes = args.wall_minutes
    job.num_nodes = args.num_nodes
    job.processes_per_node = args.processes_per_node
    job.threads_per_rank = args.threads_per_rank
    job.threads_per_core = args.threads_per_core

    job.application = args.application
    job.application_args = ' '.join(args.args)
    job.preprocess = args.preprocessor
    job.postprocess = args.postprocessor
    job.post_error_handler = args.post_handle_error
    job.post_timeout_handler = args.post_handle_timeout
    job.auto_timeout_retry = not args.disable_auto_timeout_retry
    job.input_files = ' '.join(args.input_files)

    job.stage_in_url = args.url_in
    job.stage_out_url = args.url_out
    job.stage_out_files = ' '.join(args.stage_out_files)
    job.environ_vars = ":".join(args.env)

    print(job)
    if not args.yes:
        if not cmd_confirmation('Confirm adding job to DB'):
            print("Add job aborted")
            return
    job.save()
    return job
    print("Added job to database")


def match_uniq_job(s):
    job = Job.objects.filter(job_id__icontains=s)
    if job.count() > 1:
        raise ValueError(f"More than one ID matched {s}")
    elif job.count() == 1: return job
    
    job = Job.objects.filter(name__contains=s)
    if job.count() > 1: job = Job.objects.filter(name=s)
    if job.count() > 1: 
        raise ValueError(f"More than one Job name matches {s}")
    elif job.count() == 1: return job

    raise ValueError(f"No job in local DB matched {s}")

def newdep(args):
    parent = match_uniq_job(args.parent)
    child = match_uniq_job(args.child)
    dag.add_dependency(parent, child)
    print(f"Created link {parent.first().cute_id} --> {child.first().cute_id}")

def ls(args):
    objects = args.objects
    name = args.name
    history = args.history
    verbose = args.verbose
    id = args.id
    tree = args.tree
    wf = args.wf

    if objects.startswith('job'):
        lscmd.ls_jobs(name, history, id, verbose, tree, wf)
    elif objects.startswith('app'):
        lscmd.ls_apps(name, id, verbose)
    elif objects.startswith('work') or objects.startswith('wf'):
        lscmd.ls_wf(name, verbose, tree, wf)

def modify(args):
    if args.obj_type == 'jobs': cls = Job
    elif args.obj_type == 'apps': cls = AppDef

    item = cls.objects.filter(pk__contains=args.id)
    if item.count() == 0:
        raise RuntimeError(f"no matching {args.obj_type}")
    elif item.count() > 1:
        raise RuntimeError(f"more than one matching {args.obj_type}")
    item = item.first()

    target_type = type(getattr(item, args.attr))
    new_value = target_type(args.value)
    if args.attr == 'state':
        item.update_state(new_value, 'User mutated state from command line')
    else:
        setattr(item, args.attr, new_value)
        item.save()
    print(f'{args.obj_type[:-1]} {args.attr} changed to:  {new_value}')


def rm(args):
    objects_name = args.objects
    name = args.name
    objid = args.id
    deleteall = args.all
    force = args.force

    # Are we removing jobs or apps?
    if objects_name.startswith('job'): cls = Job
    elif objects_name.startswith('app'): cls = AppDef
    objects = cls.objects.all()

    # Filter: all objects, by name-match (multiple), or by ID (unique)?
    if deleteall:
        deletion_objs = objects
        message = f"ALL {objects_name}"
    elif name: 
        deletion_objs = objects.filter(name__icontains=name)
        message = f"{len(deletion_objs)} {objects_name} matching name {name}"
        if not deletion_objs.exists(): 
            print("No {objects_name} matching query")
            return
    elif objid: 
        deletion_objs = objects.filter(pk__icontains=objid)
        if deletion_objs.count() > 1:
            raise RuntimeError(f"Multiple {objects_name} match ID")
        elif deletion_objs.count() == 0:
            raise RuntimeError(f"No {objects_name} match ID")
        else:
            message = f"{objects_name[:-1]} with ID matching {objid}"
    
    # User confirmation
    if not force:
        if not cmd_confirmation(f"PERMANENTLY remove {message}?"):
            print("Delete aborted")
            return

    # Actually delete things here
    for obj in deletion_objs:
        msg = f"Deleted {objects_name[:-1]} {obj.cute_id}"
        obj.delete()
        print(msg)


def qsub(args):
    job = Job()
    job.name = args.name
    job.description = 'Added by balsam qsub'
    job.workflow = 'qsub'
    job.allowed_work_sites = settings.BALSAM_SITE

    job.wall_time_minutes = args.wall_minutes
    job.num_nodes = args.nodes
    job.processes_per_node = args.ppn
    job.threads_per_rank = args.threads_per_rank
    job.threads_per_core = args.threads_per_core
    job.environ_vars = ":".join(args.env)

    job.application = ''
    job.application_args = ''
    job.preprocess = ''
    job.postprocess = ''
    job.post_error_handler = False
    job.post_timeout_handler = False
    job.auto_timeout_retry = False
    job.input_files = ''
    job.stage_in_url = ''
    job.stage_out_url = ''
    job.stage_out_files = ''
    job.direct_command = ' '.join(args.command)

    print(job)
    job.save()
    print("Added to database")

def kill(args):
    job_id = args.id
    
    job = Job.objects.filter(job_id__startswith=job_id)
    if job.count() > 1:
        raise RuntimeError(f"More than one job matches {job_id}")
    if job.count() == 0:
        print(f"No jobs match the given ID {job_id}")

    job = job.first()

    if cmd_confirmation(f'Really kill job {job.name} {job.cute_id} ??'):
        dag.kill(job, recursive=args.recursive)
        print("Job killed")


def mkchild(args):
    if not dag.current_job:
        raise RuntimeError(f"mkchild requires that BALSAM_JOB_ID is in the environment")
    child_job = newjob(args)
    dag.add_dependency(dag.current_job, child_job)
    print(f"Created link {dag.current_job.cute_id} --> {child_job.cute_id}")

def launcher(args):
    daemon = args.daemon
    from importlib.util import find_spec
    fname = find_spec("balsam.launcher.launcher").origin
    original_args = sys.argv[2:]
    command = [sys.executable] + [fname] + original_args
    print("Starting Balsam launcher")
    p = subprocess.Popen(command)

    if args.daemon:
        sys.exit(0)
    else:
        p.wait()


def service(args):
    print("dummy -- invoking balsam metascheduler service")

def make_dummies(args):
    for i in range(args.num):
        job = Job()
        job.name = f'dummy{i}'
        job.description = 'Added by balsam make_dummies'
        job.workflow = 'dummy'
        job.allowed_work_sites = settings.BALSAM_SITE

        job.wall_time_minutes = 0
        job.num_nodes = 1
        job.processes_per_node = 1
        job.threads_per_rank = 1
        job.threads_per_core = 1
        job.environ_vars = ""

        job.application = ''
        job.application_args = ''
        job.preprocess = ''
        job.postprocess = ''
        job.post_error_handler = False
        job.post_timeout_handler = False
        job.auto_timeout_retry = False
        job.input_files = ''
        job.stage_in_url = ''
        job.stage_out_url = ''
        job.stage_out_files = ''
        job.direct_command = 'echo hello'

        job.save()
    print(f"Added {args.num} dummy jobs to the DB")
