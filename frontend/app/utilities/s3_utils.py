from html import escape
from pathlib import PurePosixPath
from typing import Any, Optional
from urllib.parse import quote

import boto3
import streamlit as st


def create_s3_client(
    *,
    region: str = "eu-west-2",
    profile_name: Optional[str] = None,
) -> Any:
    """Create an S3 client using the default credentials or a specified profile."""
    session = boto3.Session(profile_name=profile_name, region_name=region)
    return session.client("s3")


def download_s3_object(s3_client: Any, bucket: str, key: str, destination: str) -> None:
    """
    Approach 1

    Download an S3 object to the app container; served then through the app via a download button.

    E.g.
    s3_client = create_s3_client()
    bucket = "your-bucket"
    key = "documents/report.pdf"
    filename = PurePosixPath(key).name

    with TemporaryDirectory() as temp_dir:
        local_file = Path(temp_dir) / "download"
        download_s3_object(s3_client, bucket, key, str(local_file))
        file_data = local_file.read_bytes()

    st.download_button(
        label=filename,
        data=file_data,
        file_name=filename,
        mime="application/octet-stream",
    )
    """
    s3_client.download_file(bucket, key, destination)


# For this setup, maybe use st.dataframe with single-row selection, 
    # then show a download button below it for the selected file. 
# The app fetches that file from S3 and serves it to the user, 
#     so their browser doesn’t need S3 access.


def render_s3_download_link(s3_client: Any, bucket: str, key: str) -> None:
    """
    Approach 2

    Render a short-lived download link labelled with the object filename.
    Sends the browser directly to the S3 object).
    User doesn't need S3 credentials: 
        The presigned URL temporarily grants access using the app’s permissions. 
        However, their browser still needs network access to the S3 endpoint, and the bucket policy must allow that request.
    """
    filename = PurePosixPath(key).name
    url = s3_client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": bucket,
            "Key": key,
            "ResponseContentDisposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
        },
        ExpiresIn=300,
    )
    st.markdown(f'<a href="{escape(url, quote=True)}">{escape(filename)}</a>', unsafe_allow_html=True)