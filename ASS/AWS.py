import boto3
import os
from botocore.exceptions import ClientError, NoCredentialsError

class AWS:

    def __init__(self, logger):
        self.aws_authenticated = False
        self.set_logger(logger)
        self._set_account_id()
        self._set_region()
        self.boto3_client_map = dict()
        pass

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
            if versioning_status['Status'] == "Enabled" or versioning_status['Status'] == "Suspended":
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
            for key in s3.list_objects(Bucket=origin_bucket_name)['Contents']:
                file = key['Key']
                copy_source = {'Bucket': origin_bucket_name, 'Key': file}
                s3_resource.meta.client.copy(copy_source, backup_bucket_name, f"{origin_bucket_name}/{key['Key']}")
            self.logger.info(f"Finished backup of bucket {origin_bucket_name} to {backup_bucket_name}")
        except Exception:
            self.logger.error(f"An error occurred while taking a backup of bucket {origin_bucket_name}")
            raise

    def restore_bucket(self, bucket_name):
        try:
            origin_bucket_name = f"aws-ass-{ self.get_account_id() }-s3-backup"
            self.logger.info(f"Connect to bucket {origin_bucket_name}")
            s3 = boto3.client('s3')
            s3_resource = boto3.resource('s3')
            self.logger.info(f"Start restore of all objects in bucket {origin_bucket_name}")
            for key in s3.list_objects(Bucket=origin_bucket_name)['Contents']:
                # full path (e.g. bucket/folder/test.png)
                origin_file_key = key['Key']
                # first folder in path (e.g. bucket)
                path_bucket = origin_file_key
                # file name (e.g. test.png)
                base_name = os.path.basename(origin_file_key)
                # path (e.g. bucket/folder/test.png)
                file_name = "/".join(origin_file_key.strip("/").split('/')[1:])
                while path_bucket.count('/') != 0:
                    file_path = os.path.split(path_bucket)
                    path_bucket = file_path[0]
                if path_bucket == bucket_name:
                    copy_source = {'Bucket': origin_bucket_name, 'Key': origin_file_key}
                    if base_name != '':
                        s3_resource.meta.client.copy(copy_source, path_bucket, file_name)
            self.logger.info(f"Finished backup of bucket {origin_bucket_name} to {bucket_name}")
        except Exception:
            self.logger.error(f"An error occurred while taking a backup of bucket {origin_bucket_name}")
            raise
