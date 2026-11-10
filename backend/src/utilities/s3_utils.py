def create_bucket_if_not_exists(s3_client, bucket: str) -> None:
    try:
        s3_client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": "eu-west-2"},
        )
        print(f"Created bucket: {bucket}")
    except s3_client.exceptions.BucketAlreadyOwnedByYou:
        print(f"Bucket already exists: {bucket}")
    except Exception as e:
        print(f"Create bucket note: {e}")


# Show S3 objects as if they are in folders, even though S3 is actually flat
def print_s3_directory_structure(s3_client, bucket: str, prefix: str = "", indent: int = 0) -> None:
    paginator = s3_client.get_paginator("list_objects_v2")
    pages = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/")

    for page in pages:
        # "Folders"
        for cp in page.get("CommonPrefixes", []):
            folder = cp["Prefix"]
            name = folder[len(prefix):].rstrip("/")
            print("  " * indent + f"{name}/")
            print_s3_directory_structure(s3_client, bucket, folder, indent + 1)

        # Files directly under current level
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key == prefix:
                continue
            name = key[len(prefix):]
            if "/" not in name:
                print("  " * indent + name)