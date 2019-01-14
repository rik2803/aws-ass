# `aws-delete-tagged-cfn-stacks`: Remove _CloudFormation_ stacks tagged with `stack_deletion_order`

## What it does

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
  
*IMPORTANT*: For RDS Clusters ( _Aurora_ ), the tag needs to be on the cluster, not on the
instance in the cluster.

This can be used in combination with a _Scheduled Task_ to tear down _CloudFormation_
stacks that are not used, for example to save on costs by stopping resources outside
of business hours.

The `Dockerfile` can be used to create a Docker image.
