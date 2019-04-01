# `aws-delete-tagged-cfn-stacks`: Remove _CloudFormation_ stacks tagged with `stack_deletion_order`

[toc]

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

## Resource tags naming conventions and tag list

To solve dependency issues and include resources in the stop/start flow, these resources can
be tagged. Depending on the resource and the tag, the automatic stop and start will perform
certain actions on those resources.

The tag _namespace_ used for tagging the resources is `<prefix>:ass:<resourcetype>:<description>`, where:

* `<prefix>` is optional, if set, the _Task Definition_ for `aws-delete-tagged-cfn-stacks`
  for that account should have an environment variable `ASS_TAG_PREFIX` with the same value.
* `ass` stands for _Automatic Start and Stop_ and is required and fixed.
* `<resourcetype>` is the lowercase AWS acronym for the service (`s3`, `rds`, ...)
* `<description>` is a (very) short description of the permormed action.

### List of existing tags

* `ass:s3:clean_bucket_on_stop`: Remove all objects in a bucket before deleting the stack
  that owns the bucket.
* `ass:rds:include`: Stop the RDS instance or cluster after all stacks have been stopped and
  start the RDS instance or cluster before re-creating the _CloudFormation_ stacks.
