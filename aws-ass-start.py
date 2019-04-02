import boto3
import botocore
import logging
import sys
import random
import string
import os
import json
import datetime


def init_logger():
    logger = logging.getLogger('aws-create-deleted-tagged-cfn-stacks')
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch = logging.StreamHandler(sys.stdout)

    if 'DEBUG' in os.environ and os.environ['DEBUG'] == 1:
        logger.setLevel(logging.DEBUG)
        ch.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
        ch.setLevel(logging.INFO)

    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger


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


def get_stacknames_and_creationorder(logger, client):
    stack_list = []
    result = []
    most_recent_only_dict = dict()
    root_stacks_only_dict = dict()

    try:
        logger.info('Getting all CloudFormation Stacks ...')
        response = client.list_stacks(StackStatusFilter=['DELETE_COMPLETE'])
        logger.info('Successfully finished getting all CloudFormation templates')
        stack_list.extend(response['StackSummaries'])
        while 'NextToken' in response:
            response = client.list_stacks(StackStatusFilter=['DELETE_COMPLETE'], NextToken=response['NextToken'])
            stack_list.extend(response['StackSummaries'])

        logger.info('Retrieve the most recently deleted stacks per stack name')
        for stack in stack_list:
            stack_name = stack['StackName']
            if stack_name in most_recent_only_dict:
                if stack['DeletionTime'] > most_recent_only_dict[stack_name]['DeletionTime']:
                    most_recent_only_dict[stack_name] = stack
            else:
                most_recent_only_dict[stack_name] = stack
        logger.info("%i stacks in most recent only stack dict" % len(most_recent_only_dict))

        logger.info('Remove nested stack from remaining stack list')
        for stack in most_recent_only_dict.keys():
            if not is_nested_stack(logger, most_recent_only_dict.keys(), stack):
                root_stacks_only_dict[stack] = most_recent_only_dict[stack]
        logger.info("%i stacks in root only stack dict" % len(root_stacks_only_dict))

        logger.info('Filter remaining stacks on existence of the stack_deletion_order tag')
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

    except botocore.exceptions.NoRegionError as e:
        logger.error("No AWS Credentials provided!!!")
        raise
    except botocore.exceptions.ClientError as e:
        logger.error(e.response['Error']['Code'])
        logger.error(e.response['Error']['Message'])
        raise

    return result


def get_beanstalk_environment_deletion_order_from_state_bucket(logger, environment, state_bucket_name):
    try:
        logger.info("Get saved state data for %s from S3 bucket %s " % (environment, state_bucket_name))
        environment_dict = json.loads(boto3.resource('s3').
                                      Object(state_bucket_name, environment).
                                      get()['Body'].
                                      read().
                                      decode('utf-8'))
        logger.info("Saved data is: %s " % environment_dict)

        return environment_dict
    except:
        logger.warning("An error occurred retrieving stack information from the S3 state bucket")
        logger.warning("Skipping this beanstalk environment, because it's an environment")
        logger.warning("that wes deleted outside the stop/start setup.")
        return None


def get_deleted_beanstalk_environment_names_and_creationorder(logger, client, state_bucket_name):
    result = []

    try:
        logger.info('Getting all terminated BeanStalk environments ...')
        response = client.describe_environments(IncludeDeleted=True,
                                                IncludedDeletedBackTo=datetime.datetime(2015, 1, 1))
        logger.info('Successfully finished getting all BeanStalk environments')
        env_list = response['Environments']
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise e

    for environment in env_list:
        if environment['Status'] == 'Terminated':
            ### Get environment deletion order from s3 bucket
            environment = get_beanstalk_environment_deletion_order_from_state_bucket(logger,
                                                                                     environment['EnvironmentName'],
                                                                                     state_bucket_name)
            if environment is not None:
                result.append(environment)

    return result


def stack_exists(logger, client, stack_name):
    try:
        response = client.describe_stacks(StackName=stack_name)
        if (len(response['Stacks']) > 0 and
                response['Stacks'][0]['StackStatus'] in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']):
            return True
    except Exception:
        return False

    return False


def get_stack_template_and_create_template(logger, client, stack, template_bucket, state_bucket_name):
    waiter = client.get_waiter('stack_create_complete')
    s3_client = boto3.client('s3')
    stack_dict = {}
    retries = 3

    try:
        # First check if stack with same name already exists
        if not stack_exists(logger, client, stack['stack_name']):
            # Get parameters from state_bucket_name
            try:
                logger.info("Get saved state data for %s from S3 bucket %s " % (stack['stack_name'], state_bucket_name))
                stack_dict = json.loads(
                    boto3.resource('s3').
                        Object(state_bucket_name, stack['stack_name']).
                        get()['Body'].
                        read().
                        decode('utf-8'))
                logger.info("Saved data is: %s " % stack_dict)
            except:
                logger.warning("An error occured retrieving stack information from the S3 state bucket")
                logger.warning("Continuing without restoring data from S3")
                stack_dict['stack_parameters'] = []

            logger.info("Get template string for template %s" % stack['stack_name'])
            response = client.get_template(StackName=stack['stack_id'], TemplateStage='Processed')
            template_body = response['TemplateBody']
            logger.info("Copy the template to the template bucket %s" % template_bucket)
            s3_client.put_object(
                Bucket=template_bucket,
                Body=template_body,
                Key=stack['stack_name'],
                ServerSideEncryption='AES256'
            )
            template_url = 'https://s3.amazonaws.com/' + template_bucket + '/' + stack['stack_name']

            for counter in range(0, retries):
                logger.info("Create the CloudFormation stack from the template of the deleted stack")
                client.create_stack(
                    StackName=stack['stack_name'],
                    TemplateURL=template_url,
                    Parameters=stack_dict['stack_parameters'],
                    Capabilities=['CAPABILITY_NAMED_IAM'],
                    Tags=stack['stack_tags']
                )

                logger.info("Wait for creation of the stack to finish, iteration %i out of %i" % (counter + 1, retries))
                try:
                    waiter.wait(StackName=stack['stack_name'])
                    logger.info("Stack creation finished in  iteration %i out of %i" % (counter + 1, retries))
                    # Leave the loop upon success
                    break
                except botocore.exceptions.WaiterError as e:
                    if counter == retries - 1:
                        logger.error("Stack re-creation for %s has failed, check the CloudFormation logs." %
                                     stack['stack_name'])
                        logger.error(e)
                        raise
                    else:
                        logger.warning("Stack creation failed, retrying after deletion ...")
                        logger.info("Start deletion of stack %s" % stack['stack_name'])
                        try:
                            client.delete_stack(StackName=stack['stack_name'])
                            client.get_waiter('stack_delete_complete').wait(StackName=stack['stack_name'])
                            logger.info("Deletion of stack %s was successful" % stack['stack_name'])
                        except:
                            logger.error("An error occurred while deleting stack %s" % stack['stack_name'])
                            logger.error("No use to retry when stack already exists (in a failed state).")
                            raise

        else:
            logger.warning("Skipping creation of stack %s because stack with same name already exists" %
                           stack['stack_name'])


    except Exception as e:
        if e.response['Error']['Code'] == 'AlreadyExistsException':
            logger.warning(
                "The stack already exists and probably is in a ROLLBACK_COMPLETE state and needs manual removal")
        raise


def create_template_bucket(logger, bucket):
    try:
        logger.info("Connect to bucket %s" % bucket)
        s3 = boto3.resource('s3')
        logger.info("Start creation of bucket %s" % bucket)
        s3.create_bucket(Bucket=bucket,
                         CreateBucketConfiguration={'LocationConstraint': get_region()})
        logger.info("Finished creation of bucket %s" % bucket)
    except Exception:
        logger.error("An error occurred while creating bucket %s" % bucket)
        raise


def remove_template_bucket(logger, bucket):
    try:
        logger.info("Connect to bucket %s" % bucket)
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket)
        logger.info("Start deletion of all objects in bucket %s" % bucket)
        bucket.objects.all().delete()
        logger.info("Start deletion of bucket %s" % bucket)
        bucket.delete()
        logger.info("Finished deletion of bucket %s" % bucket)
    except Exception:
        logger.error("An error occurred while deleting bucket %s" % bucket)
        raise


def start_tagged_rds_clusters_and_instances(logger):
    def start_rds(logger, rds_type, main_key, identifier_key, arn_key, status_key):

        rds_client = boto3.client('rds', region_name=get_region())

        logger.info("Get list of all RDS {}s".format(rds_type))
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

                if resource_has_tag(rds_client, arn, 'stop_or_start_with_cfn_stacks', 'yes'):
                    logger.info("RDS %s %s is tagged with %s and tag value is yes" %
                                (rds_type, arn, 'stop_or_start_with_cfn_stacks'))
                    logger.info("Starting RDS %s %s" % (rds_type, arn))
                    if status != 'stopped':
                        logger.info("RDS %s %s is in state %s ( != stopped ): Skipping start" %
                                    (rds_type, identifier, status))
                    elif rds_type == 'instance' and 'DBClusterIdentifier' in item:
                        # Skip instances that are part of a RDS Cluster, they will be processed
                        # in the DBCluster part, when rds_type is 'cluster'
                        logger.info("RDS %s %s is part of RDS Cluster %s: Skipping start" %
                                    (rds_type, item['DBInstanceIdentifier'], item['DBClusterIdentifier']))
                    else:
                        if rds_type == 'instance':
                            rds_client.start_db_instance(DBInstanceIdentifier=item['DBInstanceIdentifier'])
                        elif rds_type == 'cluster':
                            rds_client.start_db_cluster(DBClusterIdentifier=item['DBClusterIdentifier'])

                        if resource_has_tag(rds_client, arn, 'start_wait_until_available', 'yes'):
                            logger.info("RDS {} is tagged with start_wait_until_available and tag value is yes".format(identifier))
                            if rds_type == 'cluster':
                                logger.warning("No waiters in boto3 for Aurora Clusters (yet).")
                                logger.warning("Cluster start will continue in parallel.")
                            elif rds_type == 'instance':
                                logger.info("Waiting until instance {} is available".format(identifier))
                                rds_client.get_waiter('db_instance_available').wait(DBInstanceIdentifier=identifier)
                                logger.info("Instance {} is available now".format(identifier))
                            else:
                                raise Exception('rds_type must be one of instance or cluster')

                        else:
                            logger.info("Starting RDS %s %s successfully triggered" % (rds_type, arn))
                else:
                    logger.info("RDS %s %s is not tagged with %s or tag value is not yes" %
                                (rds_type, arn, 'stop_or_start_with_cfn_stacks'))
        except botocore.exceptions.NoRegionError as e:
            logger.error("No region provided!!!")
            raise
        except botocore.exceptions.NoCredentialsError as e:
            logger.error("No credentials provided!!!")
            raise

    logger.info("Starting RDS clusters and instances tagged with stop_or_start_with_cfn_stacks=yes")
    start_rds(logger, 'instance', 'DBInstances', 'DBInstanceIdentifier', 'DBInstanceArn', 'DBInstanceStatus')
    start_rds(logger, 'cluster', 'DBClusters', 'DBClusterIdentifier', 'DBClusterArn', 'Status')


def resource_has_tag(client, resource_arn, tag_name, tag_value):
    try:
        response = client.list_tags_for_resource(ResourceName=resource_arn)
        for tag in response['TagList']:
            if tag['Key'] == tag_name and tag['Value'] == tag_value:
                return True
    except Exception:
        return False

    return False


def create_deleted_tagged_cloudformation_stacks(logger, template_bucket_name, state_bucket_name):
    client = boto3.client('cloudformation', region_name=get_region())

    result = get_stacknames_and_creationorder(logger, client)

    for stack in sorted(result, key=lambda k: k['stack_deletion_order'], reverse=True):
        get_stack_template_and_create_template(logger, client, stack, template_bucket_name, state_bucket_name)
        logger.info("Creation of previously deleted tagged CloudFormation stack %s ended successfully" %
                    stack['stack_name'])

    logger.info('Creation of all previously deleted tagged CloudFormation stacks ended successfully')


def create_deleted_tagged_beanstalk_environments(logger, state_bucket_name):
    logger.info("Start creation of deleted BeanStalk environments tagged with environment_deletion_order")
    client = boto3.client('elasticbeanstalk', region_name=get_region())

    result = get_deleted_beanstalk_environment_names_and_creationorder(logger, client, state_bucket_name)

    for environment in sorted(result, key=lambda k: k['environment_deletion_order'], reverse=True):
        try:
            client.rebuild_environment(EnvironmentId=environment['environment_id'])
            logger.info("Async re-creation of terminated BeanStalk environment %s ended successfully" %
                        environment['environment_name'])
            logger.info("Please allow a few minutes for the environment to start.")
        except Exception:
            logger.error("Async re-creation of terminated BeanStalk environment %s failed" %
                         environment['environment_name'])
            raise

    logger.info('Creation of terminated BeanStalk environments ended')


def get_region():
    return (boto3.session.Session().region_name)


def get_account_id():
    return (boto3.client("sts").get_caller_identity()["Account"])


def main():
    try:
        logger = init_logger()
        region = get_region()
        account_id = get_account_id()
        state_bucket_name = "%s-%s-stop-start-state-bucket" % (region, account_id)

        logger.info("Region:       %s" % region)
        logger.info("AccountId:    %s" % account_id)
        logger.info("State Bucket: %s" % state_bucket_name)

        template_bucket_name = 'stack-recreation-bucket-' + \
                               ''.join(random.choices(string.ascii_lowercase + string.digits, k=20))
        create_template_bucket(logger, template_bucket_name)

        start_tagged_rds_clusters_and_instances(logger)
        create_deleted_tagged_cloudformation_stacks(logger, template_bucket_name, state_bucket_name)
        create_deleted_tagged_beanstalk_environments(logger, state_bucket_name)
    except Exception as e:
        logger.error("An exception occurred")
        logger.error(e)
    finally:
        if template_bucket_name:
            remove_template_bucket(logger, template_bucket_name)
        logging.shutdown()


main()
