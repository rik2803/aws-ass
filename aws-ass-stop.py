import boto3
import logging
import json
import os
from ASS import Config
from ASS import AWS
from ASS import Notification

from botocore.exceptions import ClientError
from botocore.exceptions import NoRegionError
from botocore.exceptions import NoCredentialsError
from botocore.exceptions import WaiterError


def is_nested_stack(stack):
    return 'ParentId' in stack


def get_stack_names_and_deletion_order(cfg, aws, client):
    result = []

    try:
        cfg.get_logger().info('Getting all CloudFormation Stacks ...')
        response = client.describe_stacks()
        cfg.get_logger().info('Successfully finished getting all CloudFormation templates')
        stack_list = response['Stacks']
    except NoRegionError as e:
        cfg.get_logger().error("No region provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided."
        )
        raise e

    for stack in stack_list:
        if 'Tags' in stack:
            for tag in stack['Tags']:
                if (tag['Key'] == 'stack_deletion_order' or
                        tag['Key'] == cfg.full_ass_tag('ass:cfn:deletion-order')) and int(tag['Value']) > 0:
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
                        save_stack_parameters_to_state_bucket(cfg, aws, this_stack)
                        result.append(this_stack)
    return result


def get_beanstalk_env_names_and_deletion_order(cfg, aws, client):
    result = []

    try:
        cfg.get_logger().info('Getting all BeanStalk environments ...')
        response = client.describe_environments()
        cfg.get_logger().info('Successfully finished getting all BeanStalk environments')
        env_list = response['Environments']
    except NoRegionError as e:
        cfg.get_logger().error("No region provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided."
        )
        raise e

    for environment in env_list:
        try:
            for tag in client.list_tags_for_resource(ResourceArn=environment['EnvironmentArn'])['ResourceTags']:
                if tag['Key'] == 'environment_deletion_order' and int(tag['Value']) > 0:
                    result.append({"environment_name": environment['EnvironmentName'],
                                   "environment_id": environment['EnvironmentId'],
                                   "environment_arn": environment['EnvironmentArn'],
                                   "environment_deletion_order": int(tag['Value'])
                                   })
        except:
            cfg.get_logger().error(f"Resource {environment['EnvironmentArn']} not found, continuing.")
    return result


def delete_stack(cfg, client, stack, aws):
    waiter = client.get_waiter('stack_delete_complete')

    try:
        cfg.get_logger().info("Start deletion of stack %s (deletion order is %i)" %
                              (stack['stack_name'], stack['stack_deletion_order']))
        client.delete_stack(StackName=stack['stack_name'])
        waiter.wait(StackName=stack['stack_name'])
    except WaiterError as e:
        cfg.get_logger().error(
            f"Stack deletion for {stack['stack_name']} has failed, check the CloudFormation logs.")
        cfg.get_logger().error(e)
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"Stack deletion for {stack['stack_name']} has failed, check the CloudFormation logs."
        )
        raise
    except Exception as e:
        raise e

    return True


def terminate_beanstalk_environment(cfg, aws, client, environment):
    try:
        cfg.get_logger().info("Start deletion of environment %s (deletion order is %i)" %
                              (environment['environment_name'], environment['environment_deletion_order']))
        client.terminate_environment(EnvironmentName=environment['environment_name'])
    except Exception as e:
        cfg.get_logger().error(
            f"Environment deletion for {environment['environment_name']} has failed, check the logs.")
        cfg.get_logger().error(e)
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"Stack deletion for {environment['environment_name']} has failed, check the CloudFormation logs."
        )
        raise

    return True


def get_lb_access_log_bucket(cfg, lb_client, lb, aws):
    """
    Retrieve and return the name of the bucket used to store the loadbalancer access logs (if any).

    :param cfg:
    :param lb_client:
    :param lb:
    :param aws:
    :return bucket_name:
    """

    try:
        cfg.get_logger().info('Get access log bucket name')
        response = lb_client.describe_load_balancer_attributes(LoadBalancerArn=lb)
        bucket = list(filter(lambda attr: attr['Key'] == 'access_logs.s3.bucket', response['Attributes']))
        if len(bucket) > 0:
            return bucket[0]['Value']
        else:
            return ''
    except Exception:
        cfg.get_logger().error("An error occurred while determining the loadbalancer access log bucket name")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"An error occurred while determining the loadbalancer access log bucket name"
        )
        raise


def empty_bucket(cfg, bucket, aws):
    try:
        cfg.get_logger().info(f"Connect to bucket {bucket}")
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(bucket)
        cfg.get_logger().info(f"Start deletion of all objects in bucket {bucket}")
        bucket.objects.all().delete()
        cfg.get_logger().info(f"Finished deletion of all objects in bucket {bucket}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'NoSuchBucket':
            cfg.get_logger().warning(f"Bucket ({bucket}) does not exist error when deleting objects, continuing")
    except Exception as e:
        cfg.get_logger().error(f"Error occurred while deleting all objects in {bucket}")
        cfg.get_logger().debug(e)
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"Error occurred while deleting all objects in {bucket}"
        )
        raise


def disable_lb_access_logs(cfg, lb_client, lb, aws):
    try:
        cfg.get_logger().info("Disable access logs for loadbalancer %s" % lb)
        lb_client.modify_load_balancer_attributes(
            LoadBalancerArn=lb,
            Attributes=[
                {
                    'Key': 'access_logs.s3.enabled',
                    'Value': 'false'
                },
            ]
        )
        cfg.get_logger().info(f"Access logs for loadbalancer {lb} successfully disabled")
    except Exception:
        cfg.get_logger().error(f"An error occurred while disabling the loadbalancer access logs")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"An error occurred while disabling the loadbalancer access logs"
        )
        raise


def backup_tagged_buckets(cfg, aws):
    backup_bucket_name = cfg.get_backup_bucket_name(aws.get_region(), aws.get_account_id())
    aws.create_bucket(backup_bucket_name, True)

    s3_resource = boto3.resource('s3', region_name=aws.get_region())
    try:
        cfg.get_logger().info("Start getting S3-Buckets")
        for bucket in s3_resource.buckets.all():
            bucket_name = bucket.name
            cfg.get_logger().debug(f"Checking bucket {bucket_name} for backup-and-empty tags")
            if aws.s3_has_tag(bucket_name, cfg.full_ass_tag("ass:s3:backup-and-empty-bucket-on-stop"), "yes"):
                cfg.get_logger().info(f"Bucket {bucket_name} will be backed up")
                aws.backup_bucket(bucket_name, backup_bucket_name)
    except Exception:
        cfg.get_logger().error(f"An error occurred while taking a backup of the buckets")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"An error occurred while taking a backup of the buckets"
        )
        raise


def empty_lb_access_log_buckets(cfg, aws):
    lb_client = boto3.client('elbv2', region_name=aws.get_region())

    try:
        cfg.get_logger().info("Start getting LB ARNs")
        response = lb_client.describe_load_balancers()
        lb_list = response['LoadBalancers']
        cfg.get_logger().info("Getting LB ARNs finished successfully")
    except NoRegionError:
        cfg.get_logger().error("No region provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided!!!"
        )
        raise
    except NoCredentialsError:
        cfg.get_logger().error("No credentials provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No credentials provided!!!"
        )
        raise

    for lb in lb_list:
        bucket = get_lb_access_log_bucket(cfg, lb_client, lb['LoadBalancerArn'], aws)
        disable_lb_access_logs(cfg, lb_client, lb['LoadBalancerArn'], aws)
        if bucket != '':
            empty_bucket(cfg, bucket, aws)


def empty_tagged_s3_buckets(cfg, aws):
    s3client = boto3.client('s3', region_name=aws.get_region())
    try:
        cfg.get_logger().info("Start getting bucket names")
        response = s3client.list_buckets()
        s3_list = response['Buckets']
        cfg.get_logger().debug(response)
        cfg.get_logger().debug(s3_list)
        cfg.get_logger().info("Getting bucket names finished successfully")
    except NoRegionError:
        cfg.get_logger().error("No region provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided!!!"
        )
        raise
    except NoCredentialsError:
        cfg.get_logger().error("No credentials provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No credentials provided!!!"
        )
        raise
    except Exception:
        raise

    for bucket in s3_list:
        bucket_name = bucket['Name']
        bucket_arn = f"arn:aws:s3:::{bucket_name}"
        cfg.get_logger().debug(f"Checking bucket {bucket_name} ({bucket_arn})")
        if (aws.s3_has_tag(bucket_name, cfg.full_ass_tag("ass:s3:clean-bucket-on-stop"), "yes") or
            aws.s3_has_tag(bucket_name, cfg.full_ass_tag("ass:s3:backup-and-empty-bucket-on-stop"), "yes")):
            cfg.get_logger().info(f"Bucket {bucket_name} will be cleaned")
            aws.empty_bucket(bucket)


def empty_cloudfront_access_log_buckets(cfg, aws):
    s3_client = boto3.client('s3', region_name=aws.get_region())
    cloudfront_client = boto3.client('cloudfront', region_name=aws.get_region())

    try:
        if 'Items' in cloudfront_client.list_distributions()['DistributionList']:
            cfg.get_logger().info("Cloudfront distribution found")
            cf_distibution_items = cloudfront_client.list_distributions()['DistributionList']['Items']

            for distro in cf_distibution_items:
                if ( AWS.resource_has_tag(cloudfront_client, distro['ARN'], 'stack_deletion_order') > 0 or
                     AWS.resource_has_tag(cloudfront_client, distro['ARN'], cfg.full_ass_tag('ass:cfn:deletion-order')) > 0):
                    # Distro Id
                    distrib_id = distro['Id']
                    distrib_info = cloudfront_client.get_distribution(Id=distrib_id)
                    # Distro etag (required for updating cloudfront distro)
                    distrib_etag = distrib_info['ResponseMetadata']['HTTPHeaders']['etag']
                    distrib_config = distrib_info['Distribution']['DistributionConfig']
                    # Getting the bucket name
                    cfg.get_logger().info("Looking for Cloudfront S3 Bucket")
                    distrib_log_bucket = distrib_info['Distribution']['DistributionConfig']['Logging']['Bucket']
                    distrib_log_bucket = str(distrib_log_bucket)

                    if ".s3.amazonaws.com" in distrib_log_bucket:
                        bucket = s3_client.list_objects_v2(Bucket=distrib_log_bucket[:-17])
                        cfg.get_logger().info(f"Found the Bucket {bucket['Name']}")

                        if distrib_config['Logging']['Enabled'] is True:
                            cfg.get_logger().info(f"Disable Cloudfront logging ID: {distrib_id}")
                            distrib_config['Logging']['Enabled'] = False
                            response = cloudfront_client.update_distribution(Id=distrib_id,
                                                                             DistributionConfig=distrib_config,
                                                                             IfMatch=distrib_etag)
                            if response['ResponseMetadata']['HTTPStatusCode'] == 200:
                                if 'Contents' in bucket:
                                    aws.empty_bucket(bucket)
                                else:
                                    cfg.get_logger().info(f"Bucket already empty: {bucket['Name']}")
                            else:
                                cfg.get_logger().warning(f"Error during disabling cloudfront logging ID: {distrib_id}")
                    else:
                        cfg.get_logger().info(f"Cloudfront logging disabled ID: {distrib_id}")
                        cfg.get_logger().info("No Cloudfront logging bucket found!")
        else:
            cfg.get_logger().info("No Cloudfront distribution")
    except NoRegionError:
        cfg.get_logger().error("No region provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided!!!"
        )
        raise
    except NoCredentialsError:
        cfg.get_logger().error("No credentials provided!!!")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: No credentials provided!!!"
        )
        raise

    except cloudfront_client.exceptions.IllegalUpdate:
        cfg.get_logger().error("Error during cloudfront update!!!")
        raise

def do_pre_deletion_tasks(cfg, aws):
    if os.getenv('ASS_SKIP_PREDELETIONTASKS', '0') == '1':
        cfg.get_logger().info(f"Skipping pre deletion tasks because "
                              f"envvar ASS_SKIP_PREDELETIONTASKS is set")
        return True
    empty_cloudfront_access_log_buckets(cfg, aws)
    backup_tagged_buckets(cfg, aws)
    empty_lb_access_log_buckets(cfg, aws)
    empty_tagged_s3_buckets(cfg, aws)

    return True


def stop_tagged_rds_clusters_and_instances(cfg, aws):
    if os.getenv('ASS_SKIP_RDS', '0') == '1':
        cfg.get_logger().info(f"Skipping RDS tasks because "
                              f"envvar ASS_SKIP_RDS is set")
        return True

    def stop_rds(rds_type, main_key, identifier_key, arn_key, status_key):
        rds_client = boto3.client('rds', region_name=aws.get_region())

        cfg.get_logger().info(f"Get list of all RDS {rds_type}s")
        try:
            if rds_type == 'instance':
                response = rds_client.describe_db_instances()
            elif rds_type == 'cluster':
                response = rds_client.describe_db_clusters()
            else:
                raise Exception('rds_type should be one of instance or cluster')

            for item in response[main_key]:
                identifier = item[identifier_key]
                arn = item[arn_key]
                status = item[status_key]

                if (aws.resource_has_tag(rds_client, arn, 'stop_or_start_with_cfn_stacks', 'yes') or
                        aws.resource_has_tag(rds_client, arn, cfg.full_ass_tag('ass:rds:include'), 'yes')):
                    cfg.get_logger().info(f"RDS {rds_type} {arn} is tagged with {cfg.full_ass_tag('ass:rds:include')} "
                                          f"and tag value is yes")
                    cfg.get_logger().info(f"Stopping RDS {rds_type} {arn}")
                    if status != 'available':
                        cfg.get_logger().info(f"RDS {rds_type} {identifier} is in state {status} "
                                              f"( != available ): Skipping stop")
                    elif rds_type == 'instance' and 'DBClusterIdentifier' in item:
                        # Skip instances that are part of a RDS Cluster, they will be processed
                        # in the DBCluster part, when rds_type is 'cluster'
                        cfg.get_logger().info(f"RDS {rds_type} {item['DBInstanceIdentifier']} is part of RDS Cluster "
                                              f"{item['DBClusterIdentifier']}: Skipping stop")
                    else:
                        if rds_type == 'instance':
                            rds_client.stop_db_instance(DBInstanceIdentifier=identifier)
                        elif rds_type == 'cluster':
                            rds_client.stop_db_cluster(DBClusterIdentifier=identifier)
                        else:
                            raise Exception('rds_type should be on of instance or cluster')

                        cfg.get_logger().info(f"Stopping RDS {rds_type} {arn} successfully triggered")
                else:
                    cfg.get_logger().info(f"RDS {rds_type} {arn} is not tagged with "
                                          f"{cfg.full_ass_tag('ass:rds:include')}, or tag value is not yes")

        except NoRegionError:
            cfg.get_logger().error("No region provided!!!")
            Notification.post_message_to_google_chat(
                f"Account ID {aws.get_account_id()}: aws-ass-stop: No region provided!!!"
            )
            raise
        except NoCredentialsError:
            cfg.get_logger().error("No credentials provided!!!")
            Notification.post_message_to_google_chat(
                f"Account ID {aws.get_account_id()}: aws-ass-stop: No credentials provided!!!"
            )
            raise

        cfg.get_logger().info(f"Finished getting list of all RDS {rds_type}s")

    cfg.get_logger().info("Stopping RDS clusters and instances tagged with ass:rds:include=yes")
    stop_rds('instance', 'DBInstances', 'DBInstanceIdentifier', 'DBInstanceArn', 'DBInstanceStatus')
    stop_rds('cluster', 'DBClusters', 'DBClusterIdentifier', 'DBClusterArn', 'Status')
    cfg.get_logger().info("Finished stopping RDS clusters and instances tagged with ass:rds:include=yes")


def delete_tagged_cloudformation_stacks(cfg, aws):
    if os.getenv('ASS_SKIP_CLOUDFORMATION', '0') == '1':
        cfg.get_logger().info(f"Skipping CloudFormation template creation because "
                              f"envvar ASS_SKIP_CLOUDFORMATION is set")
        return True

    cfg.get_logger().info(
        f"Start deletion of CloudFormation stacks tagged with {cfg.full_ass_tag('ass:cfn:deletion-order')}"
    )
    client = boto3.client('cloudformation', region_name=aws.get_region())

    result = get_stack_names_and_deletion_order(cfg, aws, client)

    for stack in sorted(result, key=lambda k: k['stack_deletion_order']):
        delete_stack(cfg, client, stack, aws)
        cfg.get_logger().info("Deletion of tagged CloudFormation stack %s ended successfully" % stack['stack_name'])

    cfg.get_logger().info('Deletion of all tagged CloudFormation stacks ended successfully')


def save_stack_parameters_to_state_bucket(cfg, aws, stack):
    state_bucket_name = cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    cfg.get_logger().info(f"Saving stack information for {stack['stack_name']} to bucket {state_bucket_name}")

    try:
        cfg.get_logger().info(f"Writing stack parameters to bucket")
        boto3.resource('s3'). \
            Bucket(state_bucket_name). \
            put_object(Key=stack['stack_name'],
                       Body=json.dumps(stack))
        cfg.get_logger().info(f"Stack parameters successfully written to "
                              f"s3://{state_bucket_name}/{stack['stack_name']}")
    except Exception:
        cfg.get_logger().error(f"Error saving beanstalk environment_deletion_order to bucket")
        Notification.post_message_to_google_chat(
            f"Account ID {aws.get_account_id()}: aws-ass-stop: "
            f"Error saving beanstalk environment_deletion_order to bucket"
        )
        raise


def save_beanstalk_environment_deletion_order_to_state_bucket(cfg, aws, client, environment):
    cfg.get_logger().info(
        f"Looking for environment_deletion_order tag and saving in to bucket "
        f"{cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())}"
    )
    for tag in client.list_tags_for_resource(ResourceArn=environment['environment_arn'])['ResourceTags']:
        if tag['Key'] == 'environment_deletion_order':
            try:
                cfg.get_logger().info(f"Tag environment_deletion_order={tag['Value']} found")
                boto3.resource('s3'). \
                    Bucket(cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())). \
                    put_object(Key=environment['environment_name'],
                               Body=json.dumps(environment))
                cfg.get_logger().info(
                    f"Tag environment_deletion_order successfully written to "
                    f"s3://{cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())}/{environment['environment_name']}"
                )
            except Exception:
                cfg.get_logger().error(f"Error saving beanstalk environment_deletion_order to bucket")
                Notification.post_message_to_google_chat(
                    f"Account ID {aws.get_account_id()}: aws-ass-stop: "
                    f"Error saving beanstalk environment_deletion_order to bucket"
                )
                raise

            break


def delete_tagged_beanstalk_environments(cfg, aws):
    if os.getenv('ASS_SKIP_ELASTICBEANSTALK', '0') == '1':
        cfg.get_logger().info(f"Skipping Elastic Beanstalk tasks because "
                              f"envvar ASS_SKIP_ELASTICBEANSTALK is set")
        return True

    cfg.get_logger().info("Start deletion of BeanStalk environments tagged with environment_deletion_order")
    client = boto3.client('elasticbeanstalk', region_name=aws.get_region())

    result = get_beanstalk_env_names_and_deletion_order(cfg, aws, client)

    for environment in sorted(result, key=lambda k: k['environment_deletion_order']):
        save_beanstalk_environment_deletion_order_to_state_bucket(cfg, aws, client, environment)
        terminate_beanstalk_environment(cfg, aws, client, environment)
        cfg.get_logger().info(
            "Deletion of tagged BeanStalk environment %s ended successfully" % environment['environment_name'])

    cfg.get_logger().info('Deletion of all tagged BeanStalk environments ended successfully')


def create_state_bucket(cfg, aws):
    state_bucket_name = cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())
    try:
        cfg.get_logger().info("Create bucket %s if it does not already exist." % state_bucket_name)
        s3 = boto3.resource('s3')
        if s3.Bucket(state_bucket_name) in s3.buckets.all():
            cfg.get_logger().info("Bucket %s already exists" % state_bucket_name)
        else:
            cfg.get_logger().info("Start creation of bucket %s" % state_bucket_name)
            s3.create_bucket(Bucket=state_bucket_name,
                             CreateBucketConfiguration={'LocationConstraint': aws.get_region()})
            cfg.get_logger().info("Finished creation of bucket %s" % state_bucket_name)
    except Exception:
        raise


def main():
    try:
        cfg = Config("aws-ass-stop")
        aws = AWS(cfg.get_logger())
        cloudformation_s3 = cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id())

        cfg.get_logger().info("Region:       %s" % aws.get_region())
        cfg.get_logger().info("AccountId:    %s" % aws.get_account_id())
        cfg.get_logger().info("State Bucket: %s" % cfg.get_state_bucket_name(aws.get_region(), aws.get_account_id()))

        # Cloudformation stop
        aws.create_bucket(cloudformation_s3)
        do_pre_deletion_tasks(cfg, aws)
        delete_tagged_cloudformation_stacks(cfg, aws)
        delete_tagged_beanstalk_environments(cfg, aws)
        stop_tagged_rds_clusters_and_instances(cfg, aws)

        logging.shutdown()
    except Exception:
        logging.shutdown()
        raise


main()
