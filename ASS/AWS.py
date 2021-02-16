import boto3
import os
from botocore.exceptions import ClientError, NoCredentialsError

class AWS:

    def __init__(self, logger):
        self.aws_authenticated = False
        self.set_logger(logger)
        self._set_account_id()
        self._set_region()
        self.get_notification_variables()
        self.boto3_client_map = dict()
        pass

    def get_notification_variables(self):
        # Get ASS_AWS_NOTIFICATION_MODE variable from SSM Parameter store.
        self.set_list_ssmparameters(['ASS_AWS_NOTIFICATION_MODE'])
        # Get variables depending on the ASS_AWS_NOTIFICATION_MODE variable from SSM Parameter store.
        if os.environ['NOTIFICATION_MODE'] == "NONE":
            self.logger.info(f'NOTIFICATION_MODE variable available. Mode:{os.environ["NOTIFICATION_MODE"]}')
        elif os.environ['NOTIFICATION_MODE'] == "JIRA":
            self.logger.info('Getting Jira variables from Parameter store')
            self.set_list_ssmparameters(['ASS_AWS_JIRA_USER', 'ASS_AWS_JIRA_API_PASSWORD', 'ASS_AWS_JIRA_URL'])
            self.logger.info('Jira variables available')
        elif os.environ['NOTIFICATION_MODE'] == "GOOGLECHAT":
            self.logger.info('Getting Google Chat variable from Parameter store')
            self.set_list_ssmparameters(['ASS_AWS_CHATURL'])
            self.logger.info(f'Google chat variable available.')
        else:
            warning = "NOTIFICATION_MODE is unknown!!!"
            self.logger.warning(warning)
            raise Exception(warning)

        self.logger.info(f"Parameters set from SSM Parameter store")

    def set_list_ssmparameters(self, paramter_list: list):
        ssm_client = boto3.client('ssm', region_name=self.get_region())
        try:
            response = ssm_client.get_parameters(
                Names=paramter_list, WithDecryption=True)

            for parameters in response['Parameters']:
                key = parameters['Name']
                value = parameters['Value']
                os.environ[key[8:]] = value
        except Exception as e:
            self.logger.error(f"Error occurred while getting the objects from the SSM ParameterStore")
            raise


    def set_logger(self, logger):
        if logger.__module__ and logger.__module__ == 'logging':
            self.logger = logger
        else:
            raise Exception("Not a valid logger object")

    def empty_bucket(self, bucket):
        s3client = boto3.client('s3', region_name= self.get_region())
        bucket_name = bucket['Name']
        versioning_status = s3client.get_bucket_versioning(Bucket=bucket_name)
        try:
            self.logger.info(f"Connect to bucket {bucket_name}")
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(bucket_name)
            self.logger.info(f"Start deletion of all objects in bucket {bucket_name}")
            bucket.objects.all().delete()
            bucket.object_versions.delete()
            self.logger.info(f"Finished deletion of all objects in bucket {bucket_name}")
        except AttributeError:
            self.logger.info(f"{bucket_name} is empty")
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                self.logger.warning(f"Bucket ({bucket_name}) does not exist error when deleting objects, continuing")
        except Exception:
            self.logger.error(f"Error occurred while deleting all objects in {bucket_name}")
            raise

    def is_aws_authenticated(self):
        return self.aws_authenticated

    def s3_has_tag(self, bucket_name, tag_name, tag_value):
        self.logger.debug(f"Checking bucket {bucket_name} for tag {tag_name} with value {tag_value}")
        s3_client = boto3.client('s3')
        try:
            response = s3_client.get_bucket_tagging(Bucket=bucket_name)
            self.logger.debug(response)
            for tag in response['TagSet']:
                self.logger.debug(tag)
                if tag['Key'] == tag_name and tag['Value'] == tag_value:
                    self.logger.debug(f"Bucket {bucket_name} has tag {tag_name} with value {tag_value}")
                    return True
        except ClientError:
            self.logger.debug(f"No TagSet found or bucket nog found for bucket {bucket_name}")
            return False

    def resource_has_tag(self, client, resource_arn, tag_name, tag_value):
        self.logger.debug(f"Checking resource {resource_arn} for tag {tag_name} with value {tag_value}")
        try:
            response = client.list_tags_for_resource(ResourceName=resource_arn)
            self.logger.debug(response['TagList'])
            for tag in response['TagList']:
                if tag['Key'] == tag_name and tag['Value'] == tag_value:
                    self.logger.debug(f"Resource {resource_arn} has tag {tag_name} with value {tag_value}")
                    return True
        except Exception:
            return False

        return False

    def cfn_stack_exists(self, stack_name):
        try:
            response = self.get_boto3_client('cloudformation').describe_stacks(StackName=stack_name)
            if len(response) > 0:
                if response.get('Stacks')[0].get('StackStatus') in ['CREATE_COMPLETE', 'UPDATE_COMPLETE']:
                    return True
        except Exception:
            return False

        return False

    def get_boto3_client(self, resource_type, region_name=None):
        if resource_type not in self.boto3_client_map:
            if region_name is None:
                region_name = self.get_region()
            self.boto3_client_map[resource_type] = boto3.client(resource_type, region_name=region_name)

        return self.boto3_client_map[resource_type]

    def get_region(self):
        return self.region

    def get_account_id(self):
        return self.account_id

    def _set_region(self):
        try:
            self.region = boto3.session.Session().region_name
            self.aws_authenticated = True
        except NoCredentialsError:
            self.region = None

    def _set_account_id(self):
        try:
            self.account_id = boto3.client("sts").get_caller_identity()["Account"]
            self.aws_authenticated = True
        except NoCredentialsError:
            self.account_id = ""

    def create_bucket(self, bucket_name, private_bucket=False):
        try:
            self.logger.info(f"Create bucket {bucket_name} if it does not already exist.")
            s3 = boto3.resource('s3')
            s3_client = boto3.client('s3')
            if s3.Bucket(bucket_name) in s3.buckets.all():
                self.logger.info(f"Bucket {bucket_name} already exists")
            else:
                self.logger.info(f"Start creation of bucket {bucket_name}")
                s3.create_bucket(Bucket=bucket_name,
                                 CreateBucketConfiguration={'LocationConstraint': self.get_region()})
                if private_bucket:
                    s3_client.put_public_access_block(
                        Bucket=bucket_name,
                        PublicAccessBlockConfiguration={
                            'BlockPublicAcls': True,
                            'IgnorePublicAcls': True,
                            'BlockPublicPolicy': True,
                            'RestrictPublicBuckets': True
                        },
                    )
                self.logger.info(f"Finished creation of bucket {bucket_name}")
        except Exception:
            raise

    def remove_bucket(self, bucket_name):
        try:
            self.logger.info(f"Connect to bucket {bucket_name}")
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(bucket_name)
            self.logger.info(f"Start deletion of all objects in bucket {bucket_name}")
            bucket.objects.all().delete()
            self.logger.info(f"Start deletion of bucket {bucket_name}")
            bucket.delete()
            self.logger.info(f"Finished deletion of bucket {bucket_name}")
        except Exception:
            self.logger.error(f"An error occurred while deleting bucket {bucket_name}")
            raise

    def backup_bucket(self, origin_bucket_name, backup_bucket_name):
        try:
            self.logger.info(f"Connect to bucket {origin_bucket_name}")
            s3 = boto3.client('s3')
            s3_resource = boto3.resource('s3')
            self.logger.info(f"Start backup of all objects in bucket {origin_bucket_name}")
            # Get all objects
            bucket = s3_resource.Bucket(origin_bucket_name)
            objects = bucket.objects.all()
            for obj in objects:
                copy_source = {'Bucket': origin_bucket_name, 'Key': obj.key}
                s3_resource.meta.client.copy(copy_source, backup_bucket_name, f"{origin_bucket_name}/{obj.key}")
            self.logger.info(f"Finished backup of bucket {origin_bucket_name} to {backup_bucket_name}")
        except Exception:
            self.logger.error(f"An error occurred while taking a backup of bucket {origin_bucket_name}")
            raise

    def restore_bucket(self, bucket_name, origin_bucket_name):
        try:
            self.logger.info(f"Connect to bucket {origin_bucket_name}")
            s3 = boto3.client('s3')
            s3_resource = boto3.resource('s3')

            # Get ACL tag
            self.logger.info(f"Getting ACL from bucket: {bucket_name}")
            acl = ""
            for tag in s3.get_bucket_tagging(Bucket=f"{bucket_name}")['TagSet']:
                if tag['Key'] == "ass:s3:backup-and-empty-bucket-on-stop-acl":
                    bucket_tag = tag['Value']
                    acl = {'ACL': f"{bucket_tag}"}
                else:
                    acl = {'ACL': "private"}

            # Starting restore
            self.logger.info(f"Start restore of all objects in bucket {origin_bucket_name}")
            bucket = s3_resource.Bucket(origin_bucket_name)
            objects = bucket.objects.all()
            for obj in objects:
                # full path (e.g. bucket/folder/test.png)
                origin_file_key = obj.key
                # path (e.g. folder/test.png)
                fn_new_bucket = "/".join(origin_file_key.strip("/").split('/')[1:])
                if not origin_file_key.endswith("/"):
                    copy_source = {'Bucket': origin_bucket_name, 'Key': origin_file_key}
                    s3_resource.meta.client.copy(copy_source, bucket_name, fn_new_bucket, acl)
                    s3.delete_object(Bucket=origin_bucket_name, Key=origin_file_key)
            self.logger.info(f"Finished backup of bucket {origin_bucket_name} to {bucket_name}")
        except Exception:
            self.logger.error(f"An error occurred while taking a backup of bucket {origin_bucket_name}")
            raise
