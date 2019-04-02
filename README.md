# `aws-create-deleted-tagged-cfn-stacks`: Add _CloudFormation_ stacks from deleted stacks tagged with `stack_deletion_order`

## What it does

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

This can be used in combination with a _Scheduled Task_ to rebuild _CloudFormation_
stacks that were previously deleted using `aws-delete-tagged-cfn-stacks`, for example
to save on costs by stopping resources outside of business hours.

The `Dockerfile` can be used to create a Docker image.
