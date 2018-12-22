# `aws-delete-tagged-cfn-stacks`: Remove _CloudFormation_ stacks tagged with `stack_deletion_order`

## What it does

When running the Python script with the appropriate credentials, it will delete
all _CloudFormation_ stacks tagged with a `stack_deletion_order` tag, in increasing
order of the value of the tag.

This can be used in combination with a _Scheduled Task_ to tear down _CloudFormation_
stacks that are not used, for example to save on costs by stopping resources outside
of business hours.

The `Dockerfile` can be used to create aDocker image.
