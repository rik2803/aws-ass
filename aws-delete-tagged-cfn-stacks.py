import boto3
import botocore
import logging
import json
from ASS import Config
from ASS import AWS

from botocore.exceptions import ClientError


def is_nested_stack(stack):
    return 'ParentId' in stack


def get_stacknames_and_deletionorder(config, aws, client):
    result = []

    try:
        config.get_logger().info('Getting all CloudFromation Stacks ...')
        response = client.describe_stacks()
        config.get_logger().info('Successfully finished getting all CloudFormation templates')
        stack_list = response['Stacks']
    except botocore.exceptions.NoRegionError as e:
        config.get_logger().error("No region provided!!!")
        raise e

    for stack in stack_list:
        if 'Tags' in stack:
            for tag in stack['Tags']:
                if tag['Key'] == 'stack_deletion_order' and int(tag['Value']) > 0:
                    if not is_nested_stack(stack):
                        if 'Parameters' in stack:
                            parameters = stack['Parameters']
                        else:
                            parameters = []

                        this_stack = {"stack_name": stack['StackName'],
                                      "stack_id": stack['StackId'],
                                      "stack_deletion_order": int(tag['Value']),
                                      "stack_parameters": parameters
                                      }
                        save_stack_parameters_to_state_bucket(config, aws, this_stack)
                        result.append(this_stack)
    return result


def get_beanstalk_envnames_and_deletionorder(config, aws, client):
    result = []

    try:
        config.get_logger().info('Getting all BeanStalk environments ...')
        response = client.describe_environments()
        config.get_logger().info('Successfully finished getting all BeanStalk environments')
        env_list = response['Environments']
    except botocore.exceptions.NoRegionError as e:
        config.get_logger().error("No region provided!!!")
        raise e

    for environment in env_list:
        for tag in client.list_tags_for_resource(ResourceArn=environment['EnvironmentArn'])['ResourceTags']:
            if tag['Key'] == 'environment_deletion_order' and int(tag['Value']) > 0:
                result.append({"environment_name": environment['EnvironmentName'],
                               "environment_id": environment['EnvironmentId'],
                               "environment_arn": environment['EnvironmentArn'],
                               "environment_deletion_order": int(tag['Value'])
                               })
    return result


def delete_stack(config, client, stack):
    waiter = client.get_waiter('stack_delete_complete')

    try:
        config.get_logger().info("Start deletion of stack %s (deletion order is %i)" %
                    (stack['stack_name'], stack['stack_deletion_order']))
        client.delete_stack(StackName=stack['stack_name'])
        waiter.wait(StackName=stack['stack_name'])
    except botocore.exceptions.WaiterError as e:
        config.get_logger().error("Stack deletion for %s has failed, check the CloudFormation logs." % stack['stack_name'])
        config.get_logger().error(e)
        raise
    except Exception as e:
        raise e

    return True


def terminate_beanstalk_environment(config, aws, client, environment):
    try:
        config.get_logger().info("Start deletion of environment %s (deletion order is %i)" %
                    (environment['environment_name'], environment['environment_deletion_order']))
        client.terminate_environment(EnvironmentName=environment['environment_name'])
    except Exception as e:
        config.get_logger().error("Environment deletion for %s has failed, check the logs." % environment['environment_name'])
        config.get_logger().error(e)
        raise

    return True


def get_lb_access_log_bucket(config, lbclient, lb):
    """
    Retrieve and return the name of the bucket used to store the load balancer access logs (if any).

    :param config:
    :param lbclient:
    :param lb:
    :return bucket_name:
    """

    try:
        config.get_logger().info('Get access log bucket name')
        response = lbclient.describe_load_balancer_attributes(LoadBalancerArn=lb)
        bucket = list(filter(lambda attr: attr['Key'] == 'access_logs.s3.bucket', response['Attributes']))
        if len(bucket) > 0:
            return bucket[0]['Value']
        else:
            return ''
    except Exception:
        config.get_logger().error("An error occurred while determining the load balancer access log bucket name")
        raise


def empty_bucket(config, bucket):
    try:
        config.get_logger().info("Connect to bucket {}".format(bucket))
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket)
        config.get_logger().info("Start deletion of all objects in bucket {}".format(bucket))
        bucket.objects.all().delete()
        config.get_logger().info("Finished deletion of all objects in bucket {}".format(bucket))
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchBucket':
            config.get_logger().warning("Bucket ({}) does not exist error when deleting objects, continuing".format(bucket))
    except Exception as e:
        config.get_logger().error("Error occured while deleting all objects in {}".format(bucket))
        config.get_logger().debug(e)
        raise


def disable_lb_access_logs(config, lbclient, lb):
    try:
        config.get_logger().info("Disable access logs for load balancer %s" % lb)
        lbclient.modify_load_balancer_attributes(
            LoadBalancerArn=lb,
            Attributes=[
                {
                    'Key': 'access_logs.s3.enabled',
                    'Value': 'false'
                },
            ]
        )
        config.get_logger().info("Access logs for load balancer %s successfully disabled" % lb)
    except Exception:
        config.get_logger().error("An error occurred while disabling the load balancer access logs")
        raise


def empty_lb_access_log_buckets(config, aws):
    lbclient = boto3.client('elbv2', region_name=aws.get_region())
    lb_list = []

    try:
        config.get_logger().info("Start getting LB ARNs")
        response = lbclient.describe_load_balancers()
        lb_list = response['LoadBalancers']
        config.get_logger().info("Getting LB ARNs finished successfully")
    except botocore.exceptions.NoRegionError:
        config.get_logger().error("No region provided!!!")
        raise
    except botocore.exceptions.NoCredentialsError:
        config.get_logger().error("No credentials provided!!!")
        raise

    for lb in lb_list:
        bucket = get_lb_access_log_bucket(config, lbclient, lb['LoadBalancerArn'])
        disable_lb_access_logs(config, lbclient, lb['LoadBalancerArn'])
        if bucket != '':
            empty_bucket(config, bucket)


def empty_tagged_s3_buckets(config, aws):
    s3client = boto3.client('s3', region_name=aws.get_region())

    try:
        config.get_logger().info("Start getting bucket names")
        response = s3client.list_buckets()
        s3_list = response['Buckets']
        config.get_logger().debug(response)
        config.get_logger().debug(s3_list)
        config.get_logger().info("Getting bucket names finished successfully")
    except botocore.exceptions.NoRegionError:
        config.get_logger().error("No region provided!!!")
        raise
    except botocore.exceptions.NoCredentialsError:
        config.get_logger().error("No credentials provided!!!")
        raise
    except Exception:
        raise

    for bucket in s3_list:
        # arn:aws:s3:::ixor-redirects-doccle-support
        bucket_name = bucket['Name']
        bucket_arn = "arn:aws:s3:::{}".format(bucket_name)
        config.get_logger().debug("Checking bucket {} ({})".format(bucket_name, bucket_arn))
        if aws.check_tag_on_s3_bucket(bucket_name, config.full_ass_tag("ass:s3:clean-bucket-on-stop"), "yes"):
            config.get_logger().info("Bucket {} will be cleaned".format(bucket_name))
            aws.empty_bucket(bucket_name)


def do_pre_deletion_tasks(config, aws):
    empty_lb_access_log_buckets(config, aws)
    empty_tagged_s3_buckets(config, aws)

    return True


def stop_tagged_rds_clusters_and_instances(config, aws):
    def stop_rds(rds_type, main_key, identifier_key, arn_key, status_key):
        rds_client = boto3.client('rds', region_name=aws.get_region())

        config.get_logger().info("Get list of all RDS {}s".format(rds_type))
        try:
            if rds_type == 'instance':
                response = rds_client.describe_db_instances()
            elif rds_type == 'cluster':
                response = rds_client.describe_db_clusters()
            else:
                raise Exception('rds_type should be on of instance or cluster')

            for item in response[main_key]:
                identifier = item[identifier_key]
                arn = item[arn_key]
                status = item[status_key]

                if resource_has_tag(config, rds_client, arn, 'stop_or_start_with_cfn_stacks', 'yes') or resource_has_tag(config, rds_client, arn, 'ass_rds_include', 'yes'):
                    config.get_logger().info("RDS {} {} is tagged with {} or {} and tag value is yes".format(rds_type, arn, 'stop_or_start_with_cfn_stacks', 'ass_rds_include'))
                    config.get_logger().info("Stopping RDS %s %s" % (rds_type, arn))
                    if status != 'available':
                        config.get_logger().info("RDS %s %s is in state %s ( != available ): Skipping stop" %
                                    (rds_type, identifier, status))
                    elif rds_type == 'instance' and 'DBClusterIdentifier' in item:
                        # Skip instances that are part of a RDS Cluster, they will be processed
                        # in the DBCluster part, when rds_type is 'cluster'
                        config.get_logger().info("RDS %s %s is part of RDS Cluster %s: Skipping stop" %
                                    (rds_type, item['DBInstanceIdentifier'], item['DBClusterIdentifier']))
                    else:
                        if rds_type == 'instance':
                            rds_client.stop_db_instance(DBInstanceIdentifier=identifier)
                        elif rds_type == 'cluster':
                            rds_client.stop_db_cluster(DBClusterIdentifier=identifier)
                        else:
                            raise Exception('rds_type should be on of instance or cluster')

                        config.get_logger().info("Stopping RDS %s %s successfully triggered" % (rds_type, arn))
                else:
                    config.get_logger().info("RDS {} {} is not tagged with {} or {}, or tag value is not yes".format(rds_type, arn, 'stop_or_start_with_cfn_stacks', 'ass_rds_include'))
        except botocore.exceptions.NoRegionError:
            config.get_logger().error("No region provided!!!")
            raise
        except botocore.exceptions.NoCredentialsError:
            config.get_logger().error("No credentials provided!!!")
            raise

    config.get_logger().info("Stopping RDS clusters and instances tagged with stop_or_start_with_cfn_stacks=yes")
    stop_rds('instance', 'DBInstances', 'DBInstanceIdentifier', 'DBInstanceArn', 'DBInstanceStatus')
    stop_rds('cluster', 'DBClusters', 'DBClusterIdentifier', 'DBClusterArn', 'Status')


def resource_has_tag(config, client, resource_arn, tag_name, tag_value):
    config.get_logger().debug(resource_arn)
    try:
        response = client.list_tags_for_resource(ResourceName=resource_arn)
        config.get_logger().debug(response['TagList'])
        for tag in response['TagList']:
            if tag['Key'] == tag_name and tag['Value'] == tag_value:
                config.get_logger().debug("Resource {} has tag {} with value {}".format(resource_arn, tag_name, tag_value))
                return True
    except Exception:
        return False

    return False


def delete_tagged_cloudformation_stacks(config, aws):
    config.get_logger().info("Start deletion of CloudFormation stacks tagged with stack_deletion_order")
    client = boto3.client('cloudformation', region_name=aws.get_region())

    result = get_stacknames_and_deletionorder(config, aws, client)

    do_pre_deletion_tasks(config, aws)

    # TODO for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
    # TODO    delete_stack(config, client, stack)
    # TODO    logger.info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])

    config.get_logger().info('Deletion of all tagged CloudFormation stacks ended successfully')


def save_stack_parameters_to_state_bucket(config, aws, stack):
    state_bucket_name = config.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    config.get_logger().info("Saving stack information for %s to bucket %s" % (stack['stack_name'], state_bucket_name))

    try:
        config.get_logger().info("Writing stack parameters to bucket")
        boto3.resource('s3'). \
            Bucket(state_bucket_name). \
            put_object(Key=stack['stack_name'],
                       Body=json.dumps(stack))
        config.get_logger().info("Stack parameters successfully written to s3://%s/%s"
                    % (state_bucket_name, stack['stack_name']))
    except Exception:
        config.get_logger().error("Error saving beanstalk environment_deletion_order to bucket")
        raise


def save_beanstalk_environment_deletion_order_to_state_bucket(config, aws, client, environment):
    config.get_logger().info("Looking for environment_deletion_order tag and saving in to bucket %s" % config.get_state_bucket_name())
    for tag in client.list_tags_for_resource(ResourceArn=environment['environment_arn'])['ResourceTags']:
        if tag['Key'] == 'environment_deletion_order':
            try:
                config.get_logger().info("Tag environment_deletion_order=%s found" % tag['Value'])
                boto3.resource('s3'). \
                    Bucket(config.get_state_bucket_name()). \
                    put_object(Key=environment['environment_name'],
                               Body=json.dumps(environment))
                config.get_logger().info("Tag environment_deletion_order successfully written to s3://%s/%s"
                            % (config.get_state_bucket_name(),
                               environment['environment_name']))
            except Exception:
                config.get_logger().error("Error saving beanstalk environment_deletion_order to bucket")
                raise

            break


def delete_tagged_beanstalk_environments(config, aws):
    config.get_logger().info("Start deletion of BeanStalk environments tagged with environment_deletion_order")
    client = boto3.client('elasticbeanstalk', region_name=aws.get_region())

    result = get_beanstalk_envnames_and_deletionorder(config, aws, client)

    for environment in sorted(result, key=lambda k: k['environment_deletion_order']):
        save_beanstalk_environment_deletion_order_to_state_bucket(config, aws, client, environment)
        terminate_beanstalk_environment(config, aws, client, environment)
        config.get_logger().info("Deletion of tagged BeanStalk environment %s ended successfully" % environment['environment_name'])

    config.get_logger().info('Deletion of all tagged BeanStalk environments ended successfully')




def create_state_bucket(config, aws):
    state_bucket_name = config.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    try:
        config.get_logger().info("Create bucket %s if it does not already exist." % state_bucket_name)
        s3 = boto3.resource('s3')
        if s3.Bucket(state_bucket_name) in s3.buckets.all():
            config.get_logger().info("Bucket %s already exists" % state_bucket_name)
        else:
            config.get_logger().info("Start creation of bucket %s" % state_bucket_name)
            s3.create_bucket(Bucket=state_bucket_name,
                             CreateBucketConfiguration={'LocationConstraint': aws.get_region()})
            config.get_logger().info("Finished creation of bucket %s" % state_bucket_name)
    except Exception:
        raise


def main():
    try:
        config = Config("aws-delete-tagged-cfn-stacks")
        aws = AWS(config.get_logger())

        config.get_logger().info("Region:       %s" % aws.get_region())
        config.get_logger().info("AccountId:    %s" % aws.get_account_id())
        config.get_logger().info("State Bucket: %s" % config.get_state_bucket_name(aws.get_region(), aws.get_account_id()))

        create_state_bucket(config, aws)

        delete_tagged_cloudformation_stacks(config, aws)
# TODO        delete_tagged_beanstalk_environments(config, aws)
# TODO        stop_tagged_rds_clusters_and_instances(config, aws)

        logging.shutdown()
    except Exception:
        logging.shutdown()
        raise


main()



