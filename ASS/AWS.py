import boto3
from botocore.exceptions import ClientError, NoCredentialsError


class AWS:

    def __init__(self, logger):
        self.set_logger(logger)
        self._set_account_id()
        self._set_region()
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
        except Exception as e:
            self.logger.error("Error occured while deleting all objects in {}".format(bucket))
            raise

    def check_tag_on_s3_bucket(self, bucket_name, tag_name, tag_value):
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

    def get_region(self):
        return self.region

    def get_account_id(self):
        return self.account_id

    def _set_region(self):
        try:
            self.region = boto3.session.Session().region_name
            self.aws_authenticated = True
        except NoCredentialsError as e:
            self.region = None

    def _set_account_id(self):
        try:
            self.account_id = boto3.client("sts").get_caller_identity()["Account"]
            self.aws_authenticated = True
        except NoCredentialsError as e:
            self.account_id = ""
