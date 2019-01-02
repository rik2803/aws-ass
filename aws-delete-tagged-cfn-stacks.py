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
        raise

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

    waiter = client.get_waiter('stack_delete_complete')

    try:
        logger.info("Start deletion of stack %s (deletion order is %i)" % (stack['stack_name'], stack['stack_deletion_order']))
        client.delete_stack(StackName=stack['stack_name'])
        waiter.wait(StackName=stack['stack_name'])
    except:
        raise

    return True


def get_access_log_bucket(logger, lbclient, lb):

    try:
        logger.info('Get access log bucket name')
        response = lbclient.describe_load_balancer_attributes(LoadBalancerArn=lb)
        bucket = list(filter(lambda attr: attr['Key'] == 'access_logs.s3.bucket', response['Attributes']))
        if len(bucket) > 0:
            return(bucket[0]['Value'])
        else:
            return('')
    except:
        raise


def empty_access_logs_bucket(logger, bucket):
    try:
        logger.info("Connect to bucket %s" % bucket)
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket)
        logger.info("Start deletion of all objects in bucket %s" % bucket)
        bucket.objects.all().delete()
        logger.info("Finished deletion of all objects in bucket %s" % bucket)
    except:
        logger.error("Error occured while deleting all objects in %s" % bucket)
        raise


def disable_access_logs(logger, lbclient, lb):
    try:
        result = lbclient.modify_load_balancer_attributes(
                   LoadBalancerArn=lb,
                   Attributes=[
                       {
                           'Key': 'access_logs.s3.enabled',
                           'Value': 'false'
                       },
                   ]
                 )
    except:
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
            empty_access_logs_bucket(logger, bucket):

    return True


logger = init_logger()
client = boto3.client('cloudformation', region_name='eu-central-1')

result = get_stacknames_and_deletionorder(logger, client)

do_pre_deletion_tasks(logger)

sys.exit(0)

# for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
#     print(stack)
#     delete_stack(logger, client, stack)
#     logger.info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])
#
# logger.info('Deletion of all tagged CloudFormation stacks ended successfully')
