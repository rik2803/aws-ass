# _Automatic Stop and Start_ (ASS): Delete and recreate _CloudFormation_ stacks tagged with `stack_deletion_order`

[toc]

## Build and publish the images

The commands are run from the root of this repository.

### Build the `tryxcom/aws-ass-stop` image

```bash
docker login
docker build -f ./Dockerfile-stop -t tryxcom/aws-ass-stop . 
docker tag tryxcom/aws-ass-stop tryxcom/aws-ass-stop:latest
docker push tryxcom/aws-ass-stop:latest
```


### Build the `tryxcom/aws-ass-start` image

```bash
docker login
docker build -f ./Dockerfile-start -t tryxcom/aws-ass-start . 
docker tag tryxcom/aws-ass-start tryxcom/aws-ass-start:latest
docker push tryxcom/aws-ass-start:latest
```

## What it does

### Deletion

```
                                  Bitbucket Pipelines                                                                                               AWS
+----------------------------------------------------+             +-----------------------------------------------------------------------------------+
|                                                    |             |                                                                                   |
| +------------------------------------------------+ |             |                                                     Application Environment       |
| | aws-ixor-sandbox-delete-tagged-cfn-stacks      +---------------------+                                                                             |
| +------------------------------------------------+ |             |     |                                                                             |
|                                                    |             |     |                                                                             |
|                                                    |             |     |                                                                             |
|                                                    |             |     |                                                                             |
|                                                    |             |     |                                    +--------------------------------------+ |
+----------------------------------------------------+             |     |                                    | CloudFormation Stack 01              | |
                                                                   |     |                                    |   Tags:                              | |
                                                                   |     |                                    |                                      | |
                                          Docker Hub               |     |  ECSMgmt ECS Cluster               +--------------------------------------+ |
          +------------------------------------------+             |  +--|--------------------+                                                        |
          |                                          |             |  |  |                    |               +--------------------------------------+ |
          | +--------------------------------------+ |             |  | +V------------------+ |     Delete    | CloudFormation Stack 02              | |
          | | tryxcom/aws-delete-tagged-cfn-stacks +-------------------->  Task Definition  +----+------------>   Tags:                              | |
          | +--------------------------------------+ |             |  | +-------------------+ |  |            |     stack_deletion_order: 1          | |
          |                                          |             |  |                       |  |            +--------------------------------------+ |
          | +--------------------------------------+ |             |  |                       |  |                                                     |
          | | ....                                 | |             |  |                       |  |            +--------------------------------------+ |
          | +--------------------------------------+ |             |  |                       |  |  Delete    | CloudFormation Stack 03              | |
          |                                          |             |  |                       |  +------------>   Tags:                              | |
          +------------------------------------------+             |  |                       |               |     stack_deletion_order: 2          | |
                                                                   |  +-----------------------+               +--------------------------------------+ |
                                                                   |                                                                                   |
                                                                   +-----------------------------------------------------------------------------------+

```

When running the Python script with the appropriate credentials, it will delete:
* all _CloudFormation_ stacks tagged with a `stack_deletion_order` tag, in increasing
  order of the value of the tag.
* stop all RDS DB Instances and Clusters tagged with `stop_or_start_with_cfn_stacks` and
  value `yes`

### Re-creation

```
                                  Bitbucket Pipelines                                                                                               AWS
+----------------------------------------------------+             +-----------------------------------------------------------------------------------+
|                                                    |             |                                                                                   |
| +------------------------------------------------+ |             |                                                     Application Environment       |
| | aws-ixor-sandbox-create-deleted-tagged-cfn-st* +----------------------+                                                                            |
| +------------------------------------------------+ |             |      |                                                                            |
|                                                    |             |      |                                                                            |
|                                                    |             |      |                                                                            |
|                                                    |             |      |                                                                            |
|                                                    |             |      |                                   +--------------------------------------+ |
+----------------------------------------------------+             |      |                                   | CloudFormation Stack 01              | |
                                                                   |      |                                   |   Tags:                              | |
                                                                   |      |                                   |                                      | |
                   Docker Hub                                      |      | ECSMgmt ECS Cluster               +--------------------------------------+ |
+----------------------------------------------------+             |  +---|-------------------+                                                        |
|                                                    |             |  |   |                   |               +--------------------------------------+ |
| +------------------------------------------------+ |             |  | +-V-----------------+ |     Create    | CloudFormation Stack 03              | |
| | tryxcom/aws-create-deleted-tagged-cfn-stacks   +--------------------> Scheduled Task    +----+------------>   Tags:                              | |
| +------------------------------------------------+ |             |  | +-------------------+ |  |            |     stack_deletion_order: 2          | |
|                                                    |             |  |                       |  |            +--------------------------------------+ |
| +------------------------------------------------+ |             |  |                       |  |                                                     |
| |           ....                                 | |             |  |                       |  |            +--------------------------------------+ |
| +------------------------------------------------+ |             |  |                       |  |  Create    | CloudFormation Stack 02              | |
|                                                    |             |  |                       |  +------------>   Tags:                              | |
+----------------------------------------------------+             |  |                       |               |     stack_deletion_order: 1          | |
                                                                   |  +-----------------------+               +--------------------------------------+ |
                                                                   |                                                                                   |
                                                                   +-----------------------------------------------------------------------------------+

```

When running the Python script with the appropriate credentials, it will:

* create all deleted _CloudFormation_ stacks tagged with a `stack_deletion_order` tag,
  in decreasing order of the value of the tag
* start all RDS DB Instances and Clusters tagged with `stop_or_start_with_cfn_stacks` and
  value `yes`
* if the tag `start_wait_until_available` is present and has the value `yes`, the script will
  wait until the DB is available before continuing to start the other resources. This is
  useful when applications using the DB fail (and don't retry) when the DB is not available.
  (**NOTE**: At this moment, no `boto3` waiters are available for Aurora Clusters, this
  functionality is consequently not available for Aurora) 
  
*IMPORTANT*: For RDS Clusters ( _Aurora_ ), the tag needs to be on the cluster, not on the
instance in the cluster.

### How to trigger this?

This can be used in combination with a _Scheduled Task_ to tear down _CloudFormation_
stacks that are not used, for example to save on costs by stopping resources outside
of business hours.

The `Dockerfile` can be used to create a Docker image.

To start the task from a BB pipeline, this `bitbucket-pipelines.yml` file can serve as
an example:

```shell script
image: atlassian/pipelines-awscli

pipelines:
  custom:
    aws_ass_start:
      - step:
          name: Create all deleted CloudFormation Stacks that have the tag stack_deletion_order
          script:
            - >
              aws ecs run-task \
                --task-definition task-aws-ass-start \
                --cluster ${ECS_MGMT_CLUSTER} \
                --network-configuration "awsvpcConfiguration={subnets=[${SUBNET}],securityGroups=[${SG}]}" \
                --launch-type "FARGATE" \
                --overrides "{\"containerOverrides\": [{\"name\": \"task-aws-ass-start\", \"environment\": [{\"name\": \"CHATURL\", \"value\": \"${CHATURL}\"}, {\"name\": \"ECS_MGMT_CLUSTER\", \"value\": \"${ECS_MGMT_CLUSTER}\"}]}]}"
```

## Resource tags naming conventions and tag list

To solve dependency issues and include resources in the stop/start flow, these resources can
be tagged. Depending on the resource and the tag, the automatic stop and start will perform
certain actions on those resources.

The tag _namespace_ used for tagging the resources is `<prefix>:ass:<resourcetype>:<description>`, where:

* `<prefix>` is optional, if set, the _Task Definition_ for `aws-delete-tagged-cfn-stacks`
  for that account should have an environment variable `ASS_TAG_PREFIX` with the same value.
* `ass` stands for _Automatic Start and Stop_ and is required and fixed.
* `<resourcetype>` is the lowercase AWS acronym for the service (`s3`, `rds`, ...)
* `<description>` is a (very) short description of the performed action.

### List of existing _ASS_ tags

These tags can be prefixed as described above.

* `ass:cfn:deletion-order`: Determine the order of deletion of the _CloudFormation_ stacks.
  The `stack_deletion_order` (no prefix allowed) is still supported for backward compatibility.
* `ass:s3:clean-bucket-on-stop`: Remove all objects in a bucket before deleting the stack
  that owns the bucket.
* `ass:rds:include`: Stop the RDS instance or cluster after all stacks have been stopped and
  start the RDS instance or cluster before re-creating the _CloudFormation_ stacks.
* `ass:rds:start-wait-until-available`: After starting the RDS instance or cluster, wait until
  the RDS status is `available` before continuing. This can be useful when a DB client can not
  recover when the data source is not available. The legacy tag `start_wait_until_available` is
  still supported for backward compatibility.

## Skipping actions by setting environment variables

Setting any of the following environment variables to `1` will cause the scripts to skip
all actions related to that variable.

* `ASS_SKIP_PREDELETIONTASKS`
* `ASS_SKIP_CLOUDFORMATION`
* `ASS_SKIP_ELASTICBEANSTALK`
* `ASS_SKIP_RDS`

### Configure a scheduled task in a  Fargat ECS Cluster to regularly update the dashboard

* It is currently not possible to do this with _CloudFormation_, because the `AWS::Events::Rule`
  object does not (yet, hopefully) support the extended `EcsParameters` as described in
  [the
  API documentation](https://docs.aws.amazon.com/AmazonCloudWatchEvents/latest/APIReference/API_PutTargets.html#API_PutTargets_RequestSyntax).
* To do this in the console (the _Fargate_ cluster and the _Task Definition
  already exist):
    * Go to *CloudWatch* -> *Events* -> *Rules*
    * Create a _Rule_ with a _Schedule_ (i.e. `rate(15 minutes)`)
    * Add a target
      * Choose _ECS Task_ as the target type
      * Select the cluster
      * Select the Task Definition
      * Set _Launch Type_ to `FARGATE`
      * Configure the Network Configuration (subnets and SG)
      * Auto-assign public IP address can remain `DISABLED`
