#!/usr/bin/env python
#
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import argparse
from jinja2 import Template

CLOUDBUILD_CONFIG = 'cloudbuild.yaml'

CLOUDBUILD_TEMPLATE = """
# Copyright 2018 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

##################################################################################
## This file is generated by cloudbuild-k8s-generator.py. Do not manually edit. ##
##################################################################################

timeout: 1800s # 30m
options:
  machineType: 'N1_HIGHCPU_8'
substitutions:
  _CLUSTER_NAME: cluster-1
  _CLUSTER_LOCATION: us-central1
steps:

- id: Pull Dev Image
  name: gcr.io/cloud-builders/docker
  dir: k8s
  entrypoint: bash
  args:
  - -exc
  - |
    TAG="$$(cat ./MARKETPLACE_TOOLS_TAG)"
    docker pull "gcr.io/cloud-marketplace-tools/k8s/dev:$$TAG"
    docker tag "gcr.io/cloud-marketplace-tools/k8s/dev:$$TAG" "gcr.io/cloud-marketplace-tools/k8s/dev:local"

- id: Get Kubernetes Credentials
  name: gcr.io/cloud-builders/gcloud
  args:
  - container
  - clusters
  - get-credentials
  - '$_CLUSTER_NAME'
  - --region
  - '$_CLUSTER_LOCATION'
  - --project
  - '$PROJECT_ID'

- id: Copy kubectl Credentials
  name: gcr.io/google-appengine/debian9
  waitFor:
  - Get Kubernetes Credentials
  entrypoint: bash
  args:
  - -exc
  - |
    mkdir -p /workspace/.kube/
    cp -r $$HOME/.kube/ /workspace/

- id: Copy gcloud Credentials
  name: gcr.io/google-appengine/debian9
  waitFor:
  - Get Kubernetes Credentials
  entrypoint: bash
  args:
  - -exc
  - |
    mkdir -p /workspace/.config/gcloud/
    cp -r $$HOME/.config/gcloud/ /workspace/.config/

{%- for solution in solutions %}

- id: Build {{ solution }}
  name: gcr.io/cloud-marketplace-tools/k8s/dev:local
  env:
  - 'KUBE_CONFIG=/workspace/.kube'
  - 'GCLOUD_CONFIG=/workspace/.config/gcloud'
  # Use local Docker network named cloudbuild as described here:
  # https://cloud.google.com/cloud-build/docs/overview#build_configuration_and_build_steps
  - 'EXTRA_DOCKER_PARAMS=--net cloudbuild'
  dir: k8s/{{ solution }}
  args:
  - make
  - -j4
  - app/build

{%- endfor %}

{%- for solution in solutions %}

- id: Verify {{ solution }}
  name: gcr.io/cloud-marketplace-tools/k8s/dev:local
  waitFor:
  - Copy kubectl Credentials
  - Copy gcloud Credentials
  - Pull Dev Image
  - Build {{ solution }}
  env:
  - 'KUBE_CONFIG=/workspace/.kube'
  - 'GCLOUD_CONFIG=/workspace/.config/gcloud'
  # Use local Docker network named cloudbuild as described here:
  # https://cloud.google.com/cloud-build/docs/overview#build_configuration_and_build_steps
  - 'EXTRA_DOCKER_PARAMS=--net cloudbuild'
  dir: k8s/{{ solution }}
  args:
  - make
  - -j4
  - app/verify

{%- for extra_config in extra_configs[solution] %}

- id: Verify {{ solution }} ({{ extra_config['name'] }})
  name: gcr.io/cloud-marketplace-tools/k8s/dev:local
  waitFor:
  - Copy kubectl Credentials
  - Copy gcloud Credentials
  - Pull Dev Image
  - Build {{ solution }}
  env:
  - 'KUBE_CONFIG=/workspace/.kube'
  - 'GCLOUD_CONFIG=/workspace/.config/gcloud'
  # Use local Docker network named cloudbuild as described here:
  # https://cloud.google.com/cloud-build/docs/overview#build_configuration_and_build_steps
  - 'EXTRA_DOCKER_PARAMS=--net cloudbuild'
  # Non-default variables.
  {%- for env_var in extra_config['env_vars'] %}
  - '{{ env_var }}'
  {%- endfor %}
  dir: k8s/{{ solution }}
  args:
  - make
  - -j4
  - app/verify

{%- endfor %}

{%- endfor %}
""".strip()


def verify_cloudbuild(cloudbuild_contents):
  if not os.path.isfile(CLOUDBUILD_CONFIG):
    is_up_to_date = False
  else:
    with open(CLOUDBUILD_CONFIG, 'r') as cloudbuild_file:
      is_up_to_date = cloudbuild_file.read() == cloudbuild_contents

  return is_up_to_date


def main():
  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--verify_only',
      action='store_true',
      default=False,
      help='verify %s file' % CLOUDBUILD_CONFIG)
  args = parser.parse_args()

  # TODO(PR/346): Spark operator: "make app/verify" fails
  # TODO(ISSUE): elastic-gke-logging: "make app/verify" fails
  # TODO(ISSUE): elasticsearch: "make app/verify" fails
  skiplist = ['elastic-gke-logging', 'elasticsearch', 'spark-operator']

  # Use extra_configs to run additional deployments
  # with non-default configurations.
  extra_configs = {
    'wordpress': [
      {
        'name': 'Public service and ingress',
        'env_vars': [
          'PUBLIC_SERVICE_AND_INGRESS_ENABLED=true'
        ]
      },
      {
        'name': 'Prometheus metrics',
        'env_vars': [
          'METRICS_EXPORTER_ENABLED=true'
        ]
      },
    ]
  }

  listdir = [f for f in os.listdir('k8s')
             if os.path.isdir(os.path.join('k8s', f))]
  listdir.sort()

  solutions_to_build = []

  for solution in listdir:
    if solution in skiplist:
      print('Skipping solution: ' + solution)
    else:
      print('Adding config for solution: ' + solution)
      solutions_to_build.append(solution)

  cloudbuild_contents = Template(CLOUDBUILD_TEMPLATE).render(
      solutions=solutions_to_build, extra_configs=extra_configs) + '\n'

  if args.verify_only:
    if verify_cloudbuild(cloudbuild_contents):
      print('The %s file is up-to-date' % CLOUDBUILD_CONFIG)
      os.sys.exit(0)
    else:
      print('The %s file is not up-to-date. Please re-generate it' %
            CLOUDBUILD_CONFIG)
      os.sys.exit(1)
  else:
    with open(CLOUDBUILD_CONFIG, 'w') as cloudbuild_file:
      cloudbuild_file.write(cloudbuild_contents)


if __name__ == '__main__':
  main()
