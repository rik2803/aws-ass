import boto3
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
        try:
            self.logger.info("Connect to bucket {}".format(bucket))
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(bucket)
            self.logger.info("Start deletion of all objects in bucket {}".format(bucket))
            bucket.objects.all().delete()
            self.logger.info("Finished deletion of all objects in bucket {}".format(bucket))
        except ClientError as e:
            if e.response['Error']['Code'] == 'NoSuchBucket':
                self.logger.warning("Bucket ({}) does not exist error when deleting objects, continuing".format(bucket))
        except Exception:
            self.logger.error("Error occurred while deleting all objects in {}".format(bucket))
            raise

    def is_aws_authenticated(self):
        return self.aws_authenticated

    def s3_has_tag(self, bucket_name, tag_name, tag_value):
        self.logger.debug("Checking bucket {} for tag {} with value {}".format(bucket_name, tag_name, tag_value))
        s3_client = boto3.client('s3')
        try:
            response = s3_client.get_bucket_tagging(Bucket=bucket_name)
            self.logger.debug(response)
            for tag in response['TagSet']:
                self.logger.debug(tag)
                if tag['Key'] == tag_name and tag['Value'] == tag_value:
                    self.logger.debug("Bucket {} has tag {} with value {}".format(bucket_name, tag_name, tag_value))
                    return True
        except ClientError:
            self.logger.debug("No TagSet found or bucket nog found for bucket {}".format(bucket_name))
            return False

    def resource_has_tag(self, client, resource_arn, tag_name, tag_value):
        self.logger.debug("Checking resource {} for tag {} with value {}".format(resource_arn, tag_name, tag_value))
        try:
            response = client.list_tags_for_resource(ResourceName=resource_arn)
            self.logger.debug(response['TagList'])
            for tag in response['TagList']:
                if tag['Key'] == tag_name and tag['Value'] == tag_value:
                    self.logger.debug(
                        "Resource {} has tag {} with value {}".format(resource_arn, tag_name, tag_value))
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

    def create_bucket(self, bucket_name):
        try:
            self.logger.info("Create bucket {} if it does not already exist.".format(bucket_name))
            s3 = boto3.resource('s3')
            if s3.Bucket(bucket_name) in s3.buckets.all():
                self.logger.info("Bucket {} already exists".format(bucket_name))
            else:
                self.logger.info("Start creation of bucket {}".format(bucket_name))
                s3.create_bucket(Bucket=bucket_name,
                                 CreateBucketConfiguration={'LocationConstraint': self.get_region()})
                self.logger.info("Finished creation of bucket {}".format(bucket_name))
        except Exception:
            raise

    def remove_bucket(self, bucket_name):
        try:
            self.logger.info("Connect to bucket %s" % bucket_name)
            s3 = boto3.resource('s3')
            bucket = s3.Bucket(bucket_name)
            self.logger.info("Start deletion of all objects in bucket %s" % bucket)
            bucket.objects.all().delete()
            self.logger.info("Start deletion of bucket %s" % bucket)
            bucket.delete()
            self.logger.info("Finished deletion of bucket %s" % bucket)
        except Exception:
            self.logger.error("An error occurred while deleting bucket %s" % bucket_name)
            raise


