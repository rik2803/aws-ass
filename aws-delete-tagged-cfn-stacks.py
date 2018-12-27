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
        logger.error("No AWS Credentials provided!!!")
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


logger = init_logger()
client = boto3.client('cloudformation', region_name='eu-central-1')

result = get_stacknames_and_deletionorder(logger, client)

for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
    print(stack)
    delete_stack(logger, client, stack)
    logger.info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])

logger.info('Deletion of all tagged CloudFormation stacks ended successfully')
