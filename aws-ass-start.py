import boto3
import botocore
import logging
import json
import datetime
import os
import time
from ASS import Config
from ASS import AWS
from ASS import Notification

from botocore.exceptions import ClientError
from botocore.exceptions import NoRegionError
from botocore.exceptions import NoCredentialsError


def is_nested_stack(logger, stack_list, stack_name):
    """
    A deleted nested stack has no ParentId property, hence that cannot be used to identify a deleted stack as
    a nested stack. Using the following method instead: if the stack name starts with the name of any other
    stack (which would be the root stack) followed by a dash, it is a nested stack.
    """

    logger.debug("Checking if stack %s is a nested stack" % stack_name)

    for stack in stack_list:
        if stack_name.startswith(stack + '-'):
            logger.debug("Stack %s is a nested stack" % stack_name)
            return True

    logger.debug("Stack %s is not a nested stack" % stack_name)
    return False


def get_stack_names_and_creation_order(cfg, aws):
    stack_list = []
    result = []
    most_recent_only_dict = dict()
    root_stacks_only_dict = dict()
    client = aws.get_boto3_client('cloudformation')

    try:
        cfg.get_logger().info(f"Getting all CloudFormation Stacks ...")
        response = client.list_stacks(StackStatusFilter=['DELETE_COMPLETE'])
        cfg.get_logger().info(f"Successfully finished getting all CloudFormation templates")
        stack_list.extend(response['StackSummaries'])
        while 'NextToken' in response:
            cfg.get_logger().info("Sleeping a second between calls to list_stacks to avoid rate errors")
            time.sleep(1)
            response = client.list_stacks(StackStatusFilter=['DELETE_COMPLETE'], NextToken=response['NextToken'])
            stack_list.extend(response['StackSummaries'])

        cfg.get_logger().info(f"Retrieve the most recently deleted stacks per stack name")
        for stack in stack_list:
            stack_name = stack['StackName']
            if stack_name in most_recent_only_dict:
                if stack['DeletionTime'] > most_recent_only_dict[stack_name]['DeletionTime']:
                    most_recent_only_dict[stack_name] = stack
            else:
                most_recent_only_dict[stack_name] = stack
        cfg.get_logger().info(f"{len(most_recent_only_dict)} stacks in most recent only stack dict")

        cfg.get_logger().info(f"Remove nested stack from remaining stack list")
        for stack in most_recent_only_dict.keys():
            if not is_nested_stack(cfg.get_logger(), most_recent_only_dict.keys(), stack):
                root_stacks_only_dict[stack] = most_recent_only_dict[stack]
        cfg.get_logger().info(f"{len(root_stacks_only_dict)} stacks in root only stack dict")

        cfg.get_logger().info(f"Filter remaining stacks on existence of the stack_deletion_order tag")
        for stack_name in root_stacks_only_dict:
            response = client.describe_stacks(StackName=root_stacks_only_dict[stack_name]['StackId'])
            stack = response['Stacks'][0]

            if 'Tags' in stack:
                for tag in stack['Tags']:
                    if tag['Key'] == 'stack_deletion_order' and int(tag['Value']) > 0:
                        result.append({"stack_name": stack['StackName'],
                                       "stack_id": stack['StackId'],
                                       "stack_deletion_order": int(tag['Value']),
                                       "stack_deletion_time": stack['DeletionTime'],
                                       "stack_tags": stack['Tags']
                                       })
                        break

    except NoRegionError:
        cfg.get_logger().error(f"No AWS Credentials provided!!!")
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-start:",
            f"No AWS Credentials provided!!!"
        )
        raise
    except ClientError as e:
        cfg.get_logger().error(e.response['Error']['Code'])
        cfg.get_logger().error(e.response['Error']['Message'])
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-start:",
            f"{e.response['Error']['Message']}"
        )
        raise

    return result


def get_beanstalk_environment_deletion_order_from_state_bucket(cfg, aws, environment):
    state_bucket_name = cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    try:
        cfg.get_logger().info(f"Get saved state data for {environment} from S3 bucket {state_bucket_name}")
        environment_dict = json.loads(boto3.resource('s3').
                                      Object(state_bucket_name, environment).
                                      get()['Body'].
                                      read().
                                      decode('utf-8'))
        cfg.get_logger().info("Saved data is: %s " % environment_dict)

        return environment_dict
    except Exception:
        cfg.get_logger().warning(f"An error occurred retrieving stack information from the S3 state bucket")
        cfg.get_logger().warning(f"Skipping this beanstalk environment, because it's an environment")
        cfg.get_logger().warning(f"that was deleted outside the stop/start setup.")
        return None


def get_deleted_beanstalk_environment_names_and_creation_order(cfg, aws):
    result = []

    try:
        cfg.get_logger().info(f"Getting all terminated BeanStalk environments ...")
        response = aws.get_boto3_client('elasticbeanstalk').describe_environments(
            IncludeDeleted=True,
            IncludedDeletedBackTo=datetime.datetime(2015, 1, 1)
        )
        cfg.get_logger().info(f"Successfully finished getting all BeanStalk environments")
        env_list = response['Environments']
    except NoRegionError as e:
        cfg.get_logger().error(f"No region provided!!!")
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-start:",
            f"No AWS Region provided!!!"
        )
        raise e

    for environment in env_list:
        if environment['Status'] == 'Terminated':
            # Get environment deletion order from s3 bucket
            environment = get_beanstalk_environment_deletion_order_from_state_bucket(
                cfg, aws, environment['EnvironmentName']
            )
            if environment is not None:
                result.append(environment)

    return result


def get_stack_template_and_create_template(cfg, aws, stack):
    waiter = aws.get_boto3_client('cloudformation').get_waiter('stack_create_complete')
    s3_client = aws.get_boto3_client('s3')
    state_bucket_name = cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    stack_dict = {}
    retries = 3

    try:
        # First check if stack with same name already exists
        if not aws.cfn_stack_exists(stack['stack_name']):
            # Get parameters from state_bucket_name
            try:
                cfg.get_logger().info(f"Get saved state for {stack['stack_name']} from S3 bucket {state_bucket_name}")
                stack_dict = json.loads(
                    boto3.resource('s3').
                        Object(state_bucket_name, stack['stack_name']).
                        get()['Body'].
                        read().
                        decode('utf-8')
                )
                cfg.get_logger().info("Saved data is: %s " % stack_dict)
            except Exception as e:
                cfg.get_logger().debug(e)
                cfg.get_logger().warning("An error occurred retrieving stack information from the S3 state bucket")
                cfg.get_logger().warning("Continuing without restoring data from S3")
                Notification.send_notification(
                    f"Account ID {aws.get_account_id()} aws-ass-start:",
                    f"An error occurred retrieving stack information from the S3 state bucket"
                )
                stack_dict['stack_parameters'] = []

            cfg.get_logger().info("Get template string for template %s" % stack['stack_name'])
            response = aws.get_boto3_client('cloudformation').get_template(
                StackName=stack['stack_id'], TemplateStage='Processed'
            )
            cfg.get_logger().info("Copy the template to the template bucket %s" % cfg.get_template_bucket_name())
            s3_client.put_object(
                Bucket=cfg.get_template_bucket_name(),
                Body=response['TemplateBody'],
                Key=stack['stack_name'],
                ServerSideEncryption='AES256'
            )
            template_url = 'https://s3.amazonaws.com/' + cfg.get_template_bucket_name() + '/' + stack['stack_name']

            for counter in range(0, retries):
                cfg.get_logger().info("Create the CloudFormation stack from the template of the deleted stack")
                aws.get_boto3_client('cloudformation').create_stack(
                    StackName=stack['stack_name'],
                    TemplateURL=template_url,
                    Parameters=stack_dict['stack_parameters'],
                    Capabilities=['CAPABILITY_NAMED_IAM'],
                    Tags=stack['stack_tags']
                )

                cfg.get_logger().info(f"Wait for stack creation to finish, iteration {counter + 1} out of {retries}")
                try:
                    waiter.wait(StackName=stack['stack_name'])
                    cfg.get_logger().info("Stack creation finished in  iteration %i out of %i" % (counter + 1, retries))
                    # Leave the loop upon success
                    break
                except botocore.exceptions.WaiterError as e:
                    if counter == retries - 1:
                        cfg.get_logger().error(
                            f"Stack re-creation for {stack['stack_name']} has failed, check the CloudFormation logs."
                        )
                        cfg.get_logger().error(e)
                        Notification.send_notification(
                            f"Account ID {aws.get_account_id()} aws-ass-start:",
                            f"Stack re-creation for {stack['stack_name']} has failed, check the CloudFormation logs."
                        )
                        raise
                    else:
                        cfg.get_logger().warning("Stack creation failed, retrying after deletion ...")
                        cfg.get_logger().info("Start deletion of stack %s" % stack['stack_name'])
                        try:
                            aws.get_boto3_client('cloudformation').delete_stack(StackName=stack['stack_name'])
                            aws.get_boto3_client('cloudformation').get_waiter('stack_delete_complete') \
                                .wait(StackName=stack['stack_name'])
                            cfg.get_logger().info("Deletion of stack %s was successful" % stack['stack_name'])
                        except Exception:
                            cfg.get_logger().error("An error occurred while deleting stack {stack['stack_name']}")
                            cfg.get_logger().error("No use to retry when stack already exists (in a failed state).")
                            Notification.send_notification(
                                f"Account ID {aws.get_account_id()} aws-ass-start:",
                                f"An error occurred while deleting stack {stack['stack_name']}."
                            )
                            raise

        else:
            cfg.get_logger().warning("Skipping creation of stack %s because stack with same name already exists" %
                                     stack['stack_name'])

    except ClientError as e:
        if e.response['Error']['Code'] == 'AlreadyExistsException':
            cfg.get_logger().warning(
                "The stack already exists and probably is in a ROLLBACK_COMPLETE state and needs manual removal")
        raise


def start_tagged_rds_clusters_and_instances(cfg, aws):
    if os.getenv('ASS_SKIP_RDS', '0') == '1':
        cfg.get_logger().info(f"Skipping RDS tasks because "
                              f"envvar ASS_SKIP_RDS is set")
        return True

    def start_rds(rds_type, main_key, identifier_key, arn_key, status_key):

        rds_client = aws.get_boto3_client('rds')

        cfg.get_logger().info(f"Get list of all RDS {rds_type}s")
        try:
            if rds_type == 'instance':
                response = rds_client.describe_db_instances()
            elif rds_type == 'cluster':
                response = rds_client.describe_db_clusters()
            else:
                raise Exception('rds_type must be one of instance or cluster')

            for item in response[main_key]:
                identifier = item[identifier_key]
                arn = item[arn_key]
                status = item[status_key]

                if (aws.resource_has_tag(rds_client, arn, 'stop_or_start_with_cfn_stacks', 'yes') or
                        aws.resource_has_tag(rds_client, arn, cfg.full_ass_tag('ass:rds:include'), 'yes')):
                    cfg.get_logger().info(f"RDS {rds_type} {arn} is tagged with {cfg.full_ass_tag('ass:rds:include')} "
                                          f"and tag value is yes")
                    cfg.get_logger().info(f"Starting RDS {rds_type} {arn}")
                    if status != 'stopped':
                        cfg.get_logger().info(f"RDS {rds_type} {identifier} in state "
                                              f"{status} (!= stopped): Skipping start")
                    elif rds_type == 'instance' and 'DBClusterIdentifier' in item:
                        # Skip instances that are part of a RDS Cluster, they will be processed
                        # in the DBCluster part, when rds_type is 'cluster'
                        cfg.get_logger().info("RDS %s %s is part of RDS Cluster %s: Skipping start".format(
                            rds_type, item['DBInstanceIdentifier'], item['DBClusterIdentifier']
                        ))
                    else:
                        if rds_type == 'instance':
                            rds_client.start_db_instance(DBInstanceIdentifier=item['DBInstanceIdentifier'])
                        elif rds_type == 'cluster':
                            rds_client.start_db_cluster(DBClusterIdentifier=item['DBClusterIdentifier'])

                        if (aws.resource_has_tag(rds_client, arn, 'start_wait_until_available', 'yes') or
                                aws.resource_has_tag(
                                    rds_client, arn, cfg.full_ass_tag('ass:rds:start-wait-until-available'), 'yes')):
                            cfg.get_logger().info(f"RDS {identifier} is tagged with "
                                                  f"{cfg.full_ass_tag('ass:rds:start-wait-until-available')} "
                                                  f"and tag value is yes")
                            if rds_type == 'cluster':
                                cfg.get_logger().warning("No waiters in boto3 for Aurora Clusters (yet).")
                                cfg.get_logger().warning("Cluster start will continue in parallel.")
                            elif rds_type == 'instance':
                                cfg.get_logger().info("Waiting until instance {} is available".format(identifier))
                                rds_client.get_waiter('db_instance_available').wait(DBInstanceIdentifier=identifier)
                                cfg.get_logger().info("Instance {} is available now".format(identifier))
                            else:
                                raise ValueError('rds_type must be one of instance or cluster')

                        else:
                            cfg.get_logger().info(f"Starting RDS {rds_type} {arn} successfully triggered")
                else:
                    cfg.get_logger().info("RDS {} {} is not tagged with {} or tag value is not yes".format(
                        rds_type, arn, 'stop_or_start_with_cfn_stacks'))

        except NoRegionError:
            cfg.get_logger().error("No region provided!!!")
            Notification.send_notification(
                f"Account ID {aws.get_account_id()} aws-ass-start:",
                f"No region provided."
            )
            raise
        except NoCredentialsError:
            cfg.get_logger().error("No credentials provided!!!")
            Notification.send_notification(
                f"Account ID {aws.get_account_id()} aws-ass-start:",
                f"No credentials provided."
            )
            raise

        cfg.get_logger().info(f"Finished getting list of all RDS {rds_type}s")

    cfg.get_logger().info("Starting RDS clusters and instances tagged with ass:rds:include=yes")
    start_rds('instance', 'DBInstances', 'DBInstanceIdentifier', 'DBInstanceArn', 'DBInstanceStatus')
    start_rds('cluster', 'DBClusters', 'DBClusterIdentifier', 'DBClusterArn', 'Status')
    cfg.get_logger().info("Finished starting RDS clusters and instances tagged with ass:rds:include=yes")


def resource_has_tag(client, resource_arn, tag_name, tag_value):
    try:
        response = client.list_tags_for_resource(ResourceName=resource_arn)
        for tag in response['TagList']:
            if tag['Key'] == tag_name and tag['Value'] == tag_value:
                return True
    except Exception:
        return False

    return False


def create_deleted_tagged_cloudformation_stacks(cfg, aws):
    if os.getenv('ASS_SKIP_CLOUDFORMATION', '0') == '1':
        cfg.get_logger().info(f"Skipping CloudFormation template creation because "
                              f"envvar ASS_SKIP_CLOUDFORMATION is set")
        return True

    result = get_stack_names_and_creation_order(cfg, aws)

    for stack in sorted(result, key=lambda k: k['stack_deletion_order'], reverse=True):
        get_stack_template_and_create_template(cfg, aws, stack)
        cfg.get_logger().info(f"Creation of previously deleted tagged CloudFormation "
                              f"stack {stack['stack_name']} ended successfully")

    cfg.get_logger().info(f"Creation of all previously deleted tagged CloudFormation stacks ended successfully")


def create_deleted_tagged_beanstalk_environments(cfg, aws):
    if os.getenv('ASS_SKIP_ELASTICBEANSTALK', '0') == '1':
        cfg.get_logger().info(f"Skipping Elastic Beanstalk tasks because "
                              f"envvar ASS_SKIP_ELASTICBEANSTALK is set")
        return True

    cfg.get_logger().info(f"Start creation of deleted BeanStalk environments tagged with environment_deletion_order")

    result = get_deleted_beanstalk_environment_names_and_creation_order(cfg, aws)

    for environment in sorted(result, key=lambda k: k['environment_deletion_order'], reverse=True):
        try:
            aws.get_boto3_client('elasticbeanstalk').rebuild_environment(EnvironmentId=environment['environment_id'])
            cfg.get_logger().info(f"Async re-creation of terminated BeanStalk environment "
                                  f"{environment['environment_name']} ended successfully")
            cfg.get_logger().info(f"Please allow a few minutes for the environment to start.")
        except Exception:
            cfg.get_logger().error(f"Async re-creation of terminated BeanStalk environment "
                                   f"{environment['environment_name']} failed")
            Notification.send_notification(
                f"Account ID {aws.get_account_id()} aws-ass-start:",
                f"Async re-creation of terminated BeanStalk environment \n"
                f"{environment['environment_name']} failed")
            raise

    cfg.get_logger().info(f"Creation of terminated BeanStalk environments ended")


def restore_s3_backup(cfg, aws):
    s3_client = aws.get_boto3_client('s3')

    try:
        cfg.get_logger().info("Start getting bucket names")
        response = s3_client.list_buckets()
        s3_list = response['Buckets']
        cfg.get_logger().debug(response)
        cfg.get_logger().debug(s3_list)
        cfg.get_logger().info("Getting bucket names finished successfully")
        for bucket in s3_list:
            bucket_name = bucket['Name']
            bucket_arn = f"arn:aws:s3:::{bucket_name}"
            cfg.get_logger().debug(f"Checking bucket {bucket_name} ({bucket_arn})")
            if aws.s3_has_tag(bucket_name, cfg.full_ass_tag("ass:s3:backup-and-empty-bucket-on-stop"), "yes"):
                cfg.get_logger().info(f"Bucket {bucket_name} will be restored")
                aws.restore_bucket(bucket_name, cfg.get_backup_bucket_name(aws.get_region(), aws.get_account_id()))
    except NoRegionError:
        cfg.get_logger().error("No region provided!!!")
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-stop:",
            f"No region provided!!!"
        )
        raise
    except NoCredentialsError:
        cfg.get_logger().error("No credentials provided!!!")
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-stop:",
            f"No credentials provided!!!"
        )
        raise
    except Exception:
        raise


def main():
    cfg = Config("aws-ass-start")
    aws = AWS(cfg.get_logger())

    try:
        cfg.get_logger().info(f"Region:       {aws.get_region()}")
        cfg.get_logger().info(f"AccountId:    {aws.get_account_id()}")
        cfg.get_logger().info(f"State Bucket: {cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())}")

        aws.create_bucket(cfg.get_template_bucket_name())

        start_tagged_rds_clusters_and_instances(cfg, aws)
        create_deleted_tagged_cloudformation_stacks(cfg, aws)
        create_deleted_tagged_beanstalk_environments(cfg, aws)
        restore_s3_backup(cfg, aws)
    except Exception as e:
        cfg.get_logger().error("An exception occurred")
        cfg.get_logger().error(e)
        Notification.send_notification(
            f"Account ID {aws.get_account_id()} aws-ass-start:",
            f"An exception occured"
        )
    finally:
        if cfg.get_template_bucket_name():
            aws.remove_bucket(cfg.get_template_bucket_name())
        logging.shutdown()


main()
