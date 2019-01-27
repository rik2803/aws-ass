import boto3
import botocore
import logging
import sys
import os
import json

def init_logger():
    logger = logging.getLogger('aws-delete-tagged-cfn-stacks')
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

def is_nested_stack(logger, stack):
    if 'ParentId' in stack:
        return True
    else:
        return False

def get_stacknames_and_deletionorder(logger, client, state_bucket_name):

    result = []

    try:
        logger.info('Getting all CloudFromation Stacks ...')
        response = client.describe_stacks()
        logger.info('Successfully finished getting all CloudFormation templates')
        stack_list = response['Stacks']
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise e

    for stack in stack_list:
        if 'Tags' in stack:
            for tag in stack['Tags']:
                if tag['Key'] == 'stack_deletion_order' and int(tag['Value']) > 0:
                    if not is_nested_stack(logger, stack):
                        if 'Parameters' in stack:
                            parameters = stack['Parameters']
                        else:
                            parameters = []

                        this_stack = { "stack_name": stack['StackName'],
                                       "stack_id": stack['StackId'],
                                       "stack_deletion_order": int(tag['Value']),
                                       "stack_parameters": parameters
                                     }
                        save_stack_parameters_to_state_bucket(logger, this_stack, state_bucket_name)
                        result.append(this_stack)
    return result


def get_beanstalk_envnames_and_deletionorder(logger, client):

    result = []

    try:
        logger.info('Getting all BeanStalk environments ...')
        response = client.describe_environments()
        logger.info('Successfully finished getting all BeanStalk environments')
        env_list = response['Environments']
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise e

    for environment in env_list:
        for tag in client.list_tags_for_resource(ResourceArn=environment['EnvironmentArn'])['ResourceTags']:
            if tag['Key'] == 'environment_deletion_order' and int(tag['Value']) > 0:
                result.append({ "environment_name": environment['EnvironmentName'],
                                "environment_id": environment['EnvironmentId'],
                                "environment_arn": environment['EnvironmentArn'],
                                "environment_deletion_order": int(tag['Value'])
                               })
    return result


def delete_stack(logger, client, stack):

    waiter = client.get_waiter('stack_delete_complete')

    try:
        logger.info("Start deletion of stack %s (deletion order is %i)" % (stack['stack_name'], stack['stack_deletion_order']))
        client.delete_stack(StackName=stack['stack_name'])
        waiter.wait(StackName=stack['stack_name'])
    except botocore.exceptions.WaiterError as e:
        logger.error("Stack deletion for %s has failed, check the CloudFormation logs." % stack['stack_name'])
        logger.error(e)
        raise
    except Exception as e:
        raise e

    return True


def terminate_beanstalk_environment(logger, client, environment):
    try:
        logger.info("Start deletion of environment %s (deletion order is %i)" % (environment['environment_name'], environment['environment_deletion_order']))
        client.terminate_environment(EnvironmentName=environment['environment_name'])
    except botocore.exceptions.WaiterError as e:
        logger.error("Environment deletion for %s has failed, check the logs." % environment['environment_name'])
        logger.error(e)
        raise
    except Exception as e:
        raise e

    return True


def get_access_log_bucket(logger, lbclient, lb):

    try:
        logger.info('Get access log bucket name')
        response = lbclient.describe_load_balancer_attributes(LoadBalancerArn=lb)
        bucket = list(filter(lambda attr: attr['Key'] == 'access_logs.s3.bucket', response['Attributes']))
        if len(bucket) > 0:
            return(bucket[0]['Value'])
        else:
            return ''
    except Exception:
        raise


def empty_bucket(logger, bucket):
    try:
        logger.info("Connect to bucket %s" % bucket)
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket)
        logger.info("Start deletion of all objects in bucket %s" % bucket)
        bucket.objects.all().delete()
        logger.info("Finished deletion of all objects in bucket %s" % bucket)
    except Exception:
        logger.error("Error occured while deleting all objects in %s" % bucket)
        raise


def disable_access_logs(logger, lbclient, lb):
    try:
        logger.info("Disable access logs for load balancer %s" % lb)
        result = lbclient.modify_load_balancer_attributes(
                   LoadBalancerArn=lb,
                   Attributes=[
                       {
                           'Key': 'access_logs.s3.enabled',
                           'Value': 'false'
                       },
                   ]
                 )
        logger.info("Access logs for load balancer %s successfully disabled" % lb)
    except Exception:
        raise


def do_pre_deletion_tasks(logger):
    lbclient = boto3.client('elbv2', region_name=get_region())
    lb_list = []

    try:
        logger.info("Start getting LB ARNs")
        response = lbclient.describe_load_balancers()
        lb_list = response['LoadBalancers']
        logger.info("Getting LB ARNs done")
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise
    except botocore.exceptions.NoCredentialsError as e:
        logger.error("No credentials provided!!!")
        raise

    for lb in lb_list:
        bucket = get_access_log_bucket(logger, lbclient, lb['LoadBalancerArn'])
        disable_access_logs(logger, lbclient, lb['LoadBalancerArn'])
        if bucket != '':
            empty_bucket(logger, bucket)

    return True

def stop_tagged_rds_clusters_and_instances(logger):
    logger.info("Stopping RDS clusters and instances tagged with stop_or_start_with_cfn_stacks=yes")

    rds_client = boto3.client('rds', region_name=get_region())

    logger.info("Get list of all RDS instances")
    try:
        response = rds_client.describe_db_instances()
        for instance in response['DBInstances']:
            try:
                logger.debug("DBClusterIdentifier: %s", instance['DBClusterIdentifier'])
            except Exception:
                logger.debug("No DBClusterIdentifier property")

            if resource_has_tag(logger, rds_client, instance['DBInstanceArn'], 'stop_or_start_with_cfn_stacks', 'yes'):
                logger.info("RDS instance %s is tagged with %s and tag value is yes" % (instance['DBInstanceArn'], 'stop_or_start_with_cfn_stacks'))
                logger.info("Stopping RDS instance %s" % instance['DBInstanceArn'])
                if instance['DBInstanceStatus'] != 'available':
                    logger.info("RDS instance %s is in state %s ( != available ): Skipping stop" % (instance['DBInstanceIdentifier'], instance['DBInstanceStatus']))
                elif 'DBClusterIdentifier' in instance:
                    logger.info("RDS instance %s is part of RDS Cluster %s: Skipping stop" % (instance['DBInstanceIdentifier'], instance['DBClusterIdentifier']))
                else:
                    rds_client.stop_db_instance(DBInstanceIdentifier=instance['DBInstanceIdentifier'])
                    logger.info("Stopping RDS instance %s successfully triggered" % instance['DBInstanceArn'])
            else:
                logger.info("RDS instance %s is not tagged with %s or tag value is not yes" % (instance['DBInstanceArn'], 'stop_or_start_with_cfn_stacks'))
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise
    except botocore.exceptions.NoCredentialsError as e:
        logger.error("No credentials provided!!!")
        raise

    logger.info("Get list of all RDS clusters")
    try:
        response = rds_client.describe_db_clusters()
        for instance in response['DBClusters']:
            if resource_has_tag(logger, rds_client, instance['DBClusterArn'], 'stop_or_start_with_cfn_stacks', 'yes'):
                logger.info("RDS cluster %s is tagged with %s and tag value is not yes" % (instance['DBClusterArn'], 'stop_or_start_with_cfn_stacks'))
                logger.info("Stopping RDS cluster %s" % instance['DBClusterArn'])
                if instance['Status'] != 'available':
                    logger.info("RDS cluster %s is in state %s ( != available ): Skipping stop" % (instance['DBClusterIdentifier'], instance['Status']))
                else:
                    rds_client.stop_db_cluster(DBClusterIdentifier=instance['DBClusterIdentifier'])
                    logger.info("Stopping RDS lcuster %s successfully triggered" % instance['DBClusterArn'])
            else:
                logger.info("RDS cluster %s is not tagged with %s or tag value is not yes" % (instance['DBClusterArn'], 'stop_or_start_with_cfn_stacks'))
    except botocore.exceptions.NoRegionError as e:
        logger.error("No region provided!!!")
        raise
    except botocore.exceptions.NoCredentialsError as e:
        logger.error("No credentials provided!!!")
        raise



def resource_has_tag(logger, client, resource_arn, tag_name, tag_value):
    try:
        response = client.list_tags_for_resource(ResourceName=resource_arn)
        for tag in response['TagList']:
            if tag['Key'] == tag_name and tag['Value'] == tag_value:
                return True
    except Exception:
        return False

    return False


def delete_tagged_cloudformation_stacks(logger, state_bucket_name):
    logger.info("Start deletion of CloudFormation stacks tagged with stack_deletion_order")
    client = boto3.client('cloudformation', region_name=get_region())

    result = get_stacknames_and_deletionorder(logger, client, state_bucket_name)

    do_pre_deletion_tasks(logger)

    for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
        delete_stack(logger, client, stack)
        logger.info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])

    logger.info('Deletion of all tagged CloudFormation stacks ended successfully')


def save_stack_parameters_to_state_bucket(logger, stack, state_bucket_name):
    logger.info("Saving stack information for %s to bucket %s" % (stack['stack_name'], state_bucket_name))

    try:
        logger.info("Writing stack parameters to bucket")
        boto3.resource('s3'). \
            Bucket(state_bucket_name). \
            put_object(Key=stack['stack_name'],
                       Body=json.dumps(stack))
        logger.info("Stack parameters successfully written to s3://%s/%s"
                    % (state_bucket_name,
                       stack['stack_name']))
    except:
        logger.error("Error saving beanstalk environment_deletion_order to bucket")
        raise


def save_beanstalk_environment_deletion_order_to_state_bucket(logger, client, environment, state_bucket_name):
    logger.info("Looking for environment_deletion_order tag and saving in to bucket %s" % state_bucket_name)
    for tag in client.list_tags_for_resource(ResourceArn=environment['environment_arn'])['ResourceTags']:
        if tag['Key'] == 'environment_deletion_order':
            try:
                logger.info("Tag environment_deletion_order=%s found" % tag['Value'])
                boto3.resource('s3').\
                      Bucket(state_bucket_name).\
                      put_object(Key=environment['environment_name'],
                                 Body=json.dumps(environment))
                logger.info("Tag environment_deletion_order successfully written to s3://%s/%s"
                            % (state_bucket_name,
                               environment['environment_name']))
            except:
                logger.error("Error saving beanstalk environment_deletion_order to bucket")
                raise

            break


def delete_tagged_beanstalk_environments(logger, state_bucket_name):
    logger.info("Start deletion of BeanStalk environments tagged with environment_deletion_order")
    client = boto3.client('elasticbeanstalk', region_name=get_region())

    result = get_beanstalk_envnames_and_deletionorder(logger, client)

    for environment in sorted(result, key=lambda k: k['environment_deletion_order']):
        save_beanstalk_environment_deletion_order_to_state_bucket(logger, client, environment, state_bucket_name)
        terminate_beanstalk_environment(logger, client, environment)
        logger.info("Deletion of tagged BeanStalk environment %s ended successfully" % environment['environment_name'])

    logger.info('Deletion of all tagged BeanStalk environments ended successfully')


def get_region():
    return(boto3.session.Session().region_name)


def get_account_id():
    return(boto3.client("sts").get_caller_identity()["Account"])


def create_state_bucket(logger, state_bucket_name):
    try:
        logger.info("Create bucket %s if it does not already exist." % state_bucket_name)
        s3 = boto3.resource('s3')
        if s3.Bucket(state_bucket_name) in s3.buckets.all():
            logger.info("Bucket %s already exists" % state_bucket_name)
        else:
            logger.info("Start creation of bucket %s" % state_bucket_name)
            s3.create_bucket(Bucket=state_bucket_name,
                             CreateBucketConfiguration={'LocationConstraint': get_region()})
            logger.info("Finished creation of bucket %s" % state_bucket_name)
    except Exception:
        raise

def main():
    try:
        logger = init_logger()
        region = get_region()
        account_id = get_account_id()
        state_bucket_name = "%s-%s-stop-start-state-bucket" % (region, account_id)

        logger.info("AccountId:    %s" % region)
        logger.info("Region:       %s" % account_id)
        logger.info("State Bucket: %s" % state_bucket_name)

        boto_state_bucket = create_state_bucket(logger, state_bucket_name)

        delete_tagged_cloudformation_stacks(logger, state_bucket_name)
        delete_tagged_beanstalk_environments(logger, state_bucket_name)
        stop_tagged_rds_clusters_and_instances(logger)

    except Exception:
        raise

main()
