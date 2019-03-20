# -*- coding: utf-8 -*-
#
# Copyright 2019 Spotify AB
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import abc
from abc import abstractmethod, abstractproperty, ABCMeta
import logging
import json
import os
import subprocess

import luigi
from luigi import six
from luigi.contrib import bigquery, gcs
from luigi.task import MixinNaiveBulkComplete

logger = logging.getLogger('luigi-interface')


@six.add_metaclass(abc.ABCMeta)
class DataflowParams(object):
    """
    Defines the naming conventions for Dataflow execution params.
    For example, the Java API expects param names in lower camel case, whereas
    the Python implementation expects snake case.

    """
    @abstractproperty
    def runner(self):
        pass

    @abstractproperty
    def project(self):
        pass

    @abstractproperty
    def zone(self):
        pass

    @abstractproperty
    def region(self):
        pass

    @abstractproperty
    def staging_location(self):
        pass

    @abstractproperty
    def temp_location(self):
        pass

    @abstractproperty
    def gcp_temp_location(self):
        pass

    @abstractproperty
    def num_workers(self):
        pass

    @abstractproperty
    def autoscaling_algorithm(self):
        pass

    @abstractproperty
    def max_num_workers(self):
        pass

    @abstractproperty
    def disk_size_gb(self):
        pass

    @abstractproperty
    def worker_machine_type(self):
        pass

    @abstractproperty
    def worker_disk_type(self):
        pass

    @abstractproperty
    def job_name(self):
        pass

    @abstractproperty
    def service_account(self):
        pass

    @abstractproperty
    def network(self):
        pass

    @abstractproperty
    def subnetwork(self):
        pass

    @abstractproperty
    def labels(self):
        pass


class _CmdLineRunner(object):
    """
    Executes a given command line class in a subprocess, logging its output.
    If more complex monitoring/logging is desired, user can implement their
    own launcher class and set it in BeamDataflowJobTask.cmd_line_runner.

    """
    @staticmethod
    def run(cmd, task=None):
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True
        )
        output_lines = []
        while True:
            line = process.stdout.readline()
            if not line:
                break
            line = line.decode("utf-8")
            output_lines += [line]
            logger.info(line.rstrip("\n"))
        process.stdout.close()
        exit_code = process.wait()
        if exit_code:
            output = "".join(output_lines)
            raise subprocess.CalledProcessError(exit_code, cmd, output=output)
        return exit_code


@six.add_metaclass(ABCMeta)
class BeamDataflowJobTask(MixinNaiveBulkComplete, luigi.Task):
    """
    Luigi wrapper for a Dataflow job. Must be overridden for each Beam SDK
    with that SDK's dataflow_executable().

    For more documentation, see:
        https://cloud.google.com/dataflow/docs/guides/specifying-exec-params

    The following required Dataflow properties must be set:

    project                 # GCP project ID
    temp_location           # Cloud storage path for temporary files

    The following optional Dataflow properties can be set:

    runner                  # PipelineRunner implementation for your Beam job.
                              Default: DirectRunner
    num_workers             # The number of workers to start the task with
                              Default: Determined by Dataflow service
    autoscaling_algorithm   # The Autoscaling mode for the Dataflow job
                              Default: `THROUGHPUT_BASED`
    max_num_workers         # Used if the autoscaling is enabled
                              Default: Determined by Dataflow service
    network                 # Network in GCE to be used for launching workers
                              Default: a network named "default"
    subnetwork              # Subnetwork in GCE to be used for launching workers
                              Default: Determined by Dataflow service
    disk_size_gb            # Remote worker disk size. Minimum value is 30GB
                              Default: set to 0 to use GCP project default
    worker_machine_type     # Machine type to create Dataflow worker VMs
                              Default: Determined by Dataflow service
    job_name                # Custom job name, must be unique across project's
                              active jobs
    worker_disk_type        # Specify SSD for local disk or defaults to hard
                              disk as a full URL of disk type resource
                              Default: Determined by Dataflow service.
    service_account         # Service account of Dataflow VMs/workers
                              Default: active GCE service account
    region                  # Region to deploy Dataflow job to
                              Default: us-central1
    zone                    # Availability zone for launching workers instances
                              Default: an available zone in the specified region
    staging_location        # Cloud Storage bucket for Dataflow to stage binary
                              files
                              Default: the value of temp_location
    gcp_temp_location       # Cloud Storage path for Dataflow to stage temporary
                              files
                              Default: the value of temp_location
    labels                  # Custom GCP labels attached to the Dataflow job
                              Default: nothing
    """

    project = None
    runner = None
    temp_location = None
    staging_location = None
    gcp_temp_location = None
    num_workers = None
    autoscaling_algorithm = None
    max_num_workers = None
    network = None
    subnetwork = None
    disk_size_gb = None
    worker_machine_type = None
    job_name = None
    worker_disk_type = None
    service_account = None
    zone = None
    region = None
    labels = {}

    cmd_line_runner = _CmdLineRunner
    dataflow_params = None
    output_uris = None

    def __init__(self):
        if not isinstance(self.dataflow_params, DataflowParams):
            raise ValueError("dataflow_params must be of type DataflowParams")

    @abstractmethod
    def dataflow_executable(self):
        """
        Command representing the Dataflow executable to be run.
        For example:

        return ['java com.spotify.luigi.MyClass', '-Xmx256m']
        """
        pass

    def args(self):
        """
        Extra String arguments that will be passed to your Dataflow job.
        For example:

        return ['--setup_file=setup.py']
        """
        return []

    def before_run(self):
        """
        Hook that gets called right before the Dataflow job is launched.
        Can be used to setup any temporary files/tables, validate input, etc.
        """
        pass

    def on_successful_run(self):
        """
        Callback that gets called right after the Dataflow job has finished
        successfully but before validate_output is run.
        """
        pass

    def validate_output(self):
        """
        Callback that can be used to validate your output before it is moved to
        its final location. Returning false here will cause the job to fail, and
        output to be removed instead of published.
        """
        return True

    def file_pattern(self):
        """
        If one/some of the input target files are not in the pattern of part-*,
        we can add the key of the required target and the correct file pattern
        that should be appended in the command line here. If the input target key is not found
        in this dict, the file pattern will be assumed to be part-* for that target.

        :return A dictionary of overridden file pattern that is not part-* for the inputs
        """
        return {}

    def on_output_validation(self):
        """
        Callback that gets called after the Dataflow job has finished
        successfully if validate_output returns True.
        """
        pass

    def cleanup_on_error(self):
        """
        Callback that gets called after the Dataflow job has finished
        unsuccessfully, or validate_output returns False.
        """
        pass

    def run(self):
        cmd_line = self._mk_cmd_line()
        logger.info(' '.join(cmd_line))

        self.before_run()

        try:
            self.cmd_line_runner.run(cmd_line, self)
        except subprocess.CalledProcessError as e:
            logger.error(e, exc_info=True)
            self.cleanup_on_error()
            """
            Exit Luigi with the same exit code as the Dataflow job process, so
            users can easily exit the job with code 50 to avoid Styx retries
            https://github.com/spotify/styx/blob/master/doc/design-overview.md#workflow-state-graph
            """
            os._exit(e.returncode)

        self.on_successful_run()

        if self.validate_output():
            self.on_output_validation()
        else:
            self.cleanup_on_error()
            raise ValueError("Output validation failed")

    def _mk_cmd_line(self):
        cmd_line = self.dataflow_executable()

        cmd_line.extend(self._get_dataflow_args())
        cmd_line.extend(self.args())
        cmd_line.extend(self._format_input_args())
        cmd_line.extend(self._format_output_args())
        return cmd_line

    def _get_runner(self):
        if self.runner in [
            "DataflowRunner",
            "DirectRunner"
        ]:
            return self.runner

        elif self.runner in [
            "InProcessPipelineRunner",
            "BlockingDataflowPipelineRunner"
        ]:
            logger.warning("Using deprecated runner %s. Consider upgrading to "
                           "Beam 2.x." % self.runner)
            return self.runner

        else:
            logger.warning("Found unsupported runner %s. Defaulting to "
                           "DirectRunner." % self.runner)
            return "DirectRunner"

    def _get_dataflow_args(self):
        def f(key, value):
            return '--{}={}'.format(key, value)

        output = []

        runner = self._get_runner()
        if runner:
            output.append(f(self.dataflow_params.runner, runner))
        if self.project:
            output.append(f(self.dataflow_params.project, self.project))
        if self.zone:
            output.append(f(self.dataflow_params.zone, self.zone))
        if self.region:
            output.append(f(self.dataflow_params.region, self.region))
        if self.staging_location:
            output.append(f(self.dataflow_params.staging_location, self.staging_location))
        if self.temp_location:
            output.append(f(self.dataflow_params.temp_location, self.temp_location))
        if self.gcp_temp_location:
            output.append(f(self.dataflow_params.gcp_temp_location, self.gcp_temp_location))
        if self.num_workers:
            output.append(f(self.dataflow_params.num_workers, self.num_workers))
        if self.autoscaling_algorithm:
            output.append(f(self.dataflow_params.autoscaling_algorithm, self.autoscaling_algorithm))
        if self.max_num_workers:
            output.append(f(self.dataflow_params.max_num_workers, self.max_num_workers))
        if self.disk_size_gb:
            output.append(f(self.dataflow_params.disk_size_gb, self.disk_size_gb))
        if self.worker_machine_type:
            output.append(f(self.dataflow_params.worker_machine_type, self.worker_machine_type))
        if self.worker_disk_type:
            output.append(f(self.dataflow_params.worker_disk_type, self.worker_disk_type))
        if self.network:
            output.append(f(self.dataflow_params.network, self.network))
        if self.subnetwork:
            output.append(f(self.dataflow_params.subnetwork, self.subnetwork))
        if self.job_name:
            output.append(f(self.dataflow_params.job_name, self.job_name))
        if self.service_account:
            output.append(f(self.dataflow_params.service_account, self.service_account))
        if self.labels:
            output.append(f(self.dataflow_params.labels, json.dumps(self.labels)))

        return output

    def _format_input_args(self):
        job_input = self.input()
        if isinstance(job_input, luigi.Target):
            job_input = {"input": job_input}
        elif not isinstance(job_input, dict):
            raise ValueError("Input (requires()) must be dict type")

        if not isinstance(self.file_pattern(), dict):
            raise ValueError('file_pattern() must return a dict type')

        input_args = []

        for (name, targets) in job_input.items():
            uri_targets = luigi.task.flatten(targets)
            uris = [self._targets_to_uri_getter.get(
                uri_target.__class__)(uri_target) for uri_target in uri_targets]
            if isinstance(targets, dict):
                """
                If targets is a dict that means it had multiple outputs.
                Make the input args in that case "<input key>-<task output key>"
                """
                names = ["%s-%s" % (name, key) for key in targets.keys()]
            else:
                names = [name] * len(uris)
            for (arg_name, uri) in zip(names, uris):
                pattern = self.file_pattern().get(name, 'part-*')
                input_args.append(
                    "--%s=%s" %
                    (arg_name, uri.rstrip('/') + '/' + pattern)
                )

        return input_args

    def _format_output_args(self):
        job_output = self.output()
        if isinstance(job_output, luigi.Target):
            job_output = {"output": job_output}
        elif not isinstance(job_output, dict):
            raise ValueError("Input (requires()) must be dict type")

        self.output_uris = {}
        output_args = []

        for (name, target) in job_output.items():
            getter = self._targets_to_uri_getter.get(target.__class__)
            uri = getter(target)

            self.output_uris[name] = uri
            output_args.append("--%s=%s" % (name, uri))

        return output_args

    @property
    def _targets_to_uri_getter(self):
        return dict([
            (luigi.LocalTarget, lambda t: t.path),
            (gcs.GCSTarget, lambda t: t.path),
            (bigquery.BigQueryTarget, lambda t: "{}:{}.{}".format(
                t.project_id, t.dataset_id, t.table_id))
        ])
