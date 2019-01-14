import boto3
import botocore
import logging
import sys


def init_logger():
    logger = logging.getLogger('aws-delete-tagged-cfn-stacks')
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger

def get_stacknames_and_deletionorder(logger, client):

    stack_list = []
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
                    result.append({ "stack_name": stack['StackName'],
                                    "stack_id": stack['StackId'],
                                    "stack_deletion_order": int(tag['Value'])
                                   })
    return result


def delete_stack(logger, client, stack):

    # boto3.set_stream_logger('boto3', level=boto3.logging.DEBUG)
    # boto3.set_stream_logger('botocore', level=boto3.logging.DEBUG)
    # boto3.set_stream_logger('boto3.resources', level=boto3.logging.DEBUG)

    waiter = client.get_waiter('stack_delete_complete')

    try:
        logger.info("Start deletion of stack %s (deletion order is %i)" % (stack['stack_name'], stack['stack_deletion_order']))
        client.delete_stack(StackName=stack['stack_name'])
        waiter.wait(StackName=stack['stack_name'])
    except Exception as e:
        raise e

    # boto3.set_stream_logger('boto3', level=boto3.logging.INFO)
    # boto3.set_stream_logger('botocore', level=boto3.logging.INFO)
    # boto3.set_stream_logger('boto3.resources', level=boto3.logging.INFO)

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
    lbclient = boto3.client('elbv2', region_name='eu-central-1')
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
    logger.info("Stopping RDS clusters and instances tagged with stop_or_start_with_cfn_stacks=true")

    rds_client = boto3.client('rds', region_name='eu-central-1')

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


try:
    logger = init_logger()
    client = boto3.client('cloudformation', region_name='eu-central-1')

    result = get_stacknames_and_deletionorder(logger, client)

    do_pre_deletion_tasks(logger)

    for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
        print(stack)
        delete_stack(logger, client, stack)
        logger.info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])

    stop_tagged_rds_clusters_and_instances(logger)

    logger.info('Deletion of all tagged CloudFormation stacks ended successfully')
except Exception:
    raise
