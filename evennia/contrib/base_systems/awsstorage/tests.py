import datetime
import gzip
import pickle
import threading
from unittest import skipIf

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.utils.timezone import is_aware

_SKIP = False
try:
    from botocore.exceptions import ClientError

    from .awsstorage import aws_s3_cdn as s3boto3
except ImportError:
    _SKIP = True


try:
    from django.utils.six.moves.urllib import parse as urlparse
except ImportError:
    from urllib import parse as urlparse


try:
    from unittest import mock
except ImportError:  # Python 3.2 and below
    import mock


@skipIf(_SKIP, "botocore not installed")
class S3Boto3TestCase(TestCase):
    def setUp(self):
        self.storage = s3boto3.S3Boto3Storage(access_key="foo", secret_key="bar")
        self.storage._connections.connection = mock.MagicMock()


@skipIf(_SKIP, "botocore not installed")
class S3Boto3StorageTests(S3Boto3TestCase):
    def test_clean_name(self):
        """
        Test the base case of _clean_name
        """
        path = self.storage._clean_name("path/to/somewhere")
        self.assertEqual(path, "path/to/somewhere")

    def test_clean_name_normalize(self):
        """
        Test the normalization of _clean_name
        """
        path = self.storage._clean_name("path/to/../somewhere")
        self.assertEqual(path, "path/somewhere")

    def test_clean_name_trailing_slash(self):
        """
        Test the _clean_name when the path has a trailing slash
        """
        path = self.storage._clean_name("path/to/somewhere/")
        self.assertEqual(path, "path/to/somewhere/")

    def test_clean_name_windows(self):
        """
        Test the _clean_name when the path has a trailing slash
        """
        path = self.storage._clean_name(r"path\to\somewhere")
        self.assertEqual(path, "path/to/somewhere")

    def test_pickle_with_bucket(self):
        """
        Test that the storage can be pickled with a bucket attached
        """
        # Ensure the bucket has been used
        self.storage.bucket
        self.assertIsNotNone(self.storage._bucket)

        # Can't pickle MagicMock, but you can't pickle a real Bucket object either
        p = pickle.dumps(self.storage)
        new_storage = pickle.loads(p)

        self.assertIsInstance(new_storage._connections, threading.local)
        # Put the mock connection back in
        new_storage._connections.connection = mock.MagicMock()

        self.assertIsNone(new_storage._bucket)
        new_storage.bucket
        self.assertIsNotNone(new_storage._bucket)

    def test_pickle_without_bucket(self):
        """
        Test that the storage can be pickled, without a bucket instance
        """

        # Can't pickle a threadlocal
        p = pickle.dumps(self.storage)
        new_storage = pickle.loads(p)

        self.assertIsInstance(new_storage._connections, threading.local)

    def test_storage_url_slashes(self):
        """
        Test URL generation.
        """
        self.storage.custom_domain = "example.com"

        # We expect no leading slashes in the path,
        # and trailing slashes should be preserved.
        self.assertEqual(self.storage.url(""), "https://example.com/")
        self.assertEqual(self.storage.url("path"), "https://example.com/path")
        self.assertEqual(self.storage.url("path/"), "https://example.com/path/")
        self.assertEqual(self.storage.url("path/1"), "https://example.com/path/1")
        self.assertEqual(self.storage.url("path/1/"), "https://example.com/path/1/")

    def test_storage_save(self):
        """
        Test saving a file
        """
        name = "test_storage_save.txt"
        content = ContentFile("new content")
        self.storage.save(name, content)
        self.storage.bucket.Object.assert_called_once_with(name)

        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            content,
            ExtraArgs={
                "ContentType": "text/plain",
                "ACL": self.storage.default_acl,
            },
        )

    def test_storage_save_with_acl(self):
        """
        Test saving a file with user defined ACL.
        """
        name = "test_storage_save.txt"
        content = ContentFile("new content")
        self.storage.default_acl = "private"
        self.storage.save(name, content)
        self.storage.bucket.Object.assert_called_once_with(name)

        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            content,
            ExtraArgs={
                "ContentType": "text/plain",
                "ACL": "private",
            },
        )

    def test_content_type(self):
        """
        Test saving a file with a None content type.
        """
        name = "test_image.jpg"
        content = ContentFile("data")
        content.content_type = None
        self.storage.save(name, content)
        self.storage.bucket.Object.assert_called_once_with(name)

        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            content,
            ExtraArgs={
                "ContentType": "image/jpeg",
                "ACL": self.storage.default_acl,
            },
        )

    def test_storage_save_gzipped(self):
        """
        Test saving a gzipped file
        """
        name = "test_storage_save.gz"
        content = ContentFile("I am gzip'd")
        self.storage.save(name, content)
        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            content,
            ExtraArgs={
                "ContentType": "application/octet-stream",
                "ContentEncoding": "gzip",
                "ACL": self.storage.default_acl,
            },
        )

    def test_storage_save_gzip(self):
        """
        Test saving a file with gzip enabled.
        """
        self.storage.gzip = True
        name = "test_storage_save.css"
        content = ContentFile("I should be gzip'd")
        self.storage.save(name, content)
        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            mock.ANY,
            ExtraArgs={
                "ContentType": "text/css",
                "ContentEncoding": "gzip",
                "ACL": self.storage.default_acl,
            },
        )
        args, kwargs = obj.upload_fileobj.call_args
        content = args[0]
        zfile = gzip.GzipFile(mode="rb", fileobj=content)
        self.assertEqual(zfile.read(), b"I should be gzip'd")

    def test_storage_save_gzip_twice(self):
        """
        Test saving the same file content twice with gzip enabled.
        """
        # Given
        self.storage.gzip = True
        name = "test_storage_save.css"
        content = ContentFile("I should be gzip'd")

        # When
        self.storage.save(name, content)
        self.storage.save("test_storage_save_2.css", content)

        # Then
        obj = self.storage.bucket.Object.return_value
        obj.upload_fileobj.assert_called_with(
            mock.ANY,
            ExtraArgs={
                "ContentType": "text/css",
                "ContentEncoding": "gzip",
                "ACL": self.storage.default_acl,
            },
        )
        args, kwargs = obj.upload_fileobj.call_args
        content = args[0]
        zfile = gzip.GzipFile(mode="rb", fileobj=content)
        self.assertEqual(zfile.read(), b"I should be gzip'd")

    def test_compress_content_len(self):
        """
        Test that file returned by _compress_content() is readable.
        """
        self.storage.gzip = True
        content = ContentFile("I should be gzip'd")
        content = self.storage._compress_content(content)
        self.assertTrue(len(content.read()) > 0)

    def test_storage_open_write(self):
        """
        Test opening a file in write mode
        """
        name = "test_open_for_writïng.txt"
        content = "new content"

        # Set the encryption flag used for multipart uploads
        self.storage.encryption = True
        self.storage.reduced_redundancy = True
        self.storage.default_acl = "public-read"

        file = self.storage.open(name, "w")
        self.storage.bucket.Object.assert_called_with(name)
        obj = self.storage.bucket.Object.return_value
        # Set the name of the mock object
        obj.key = name

        file.write(content)
        obj.initiate_multipart_upload.assert_called_with(
            ACL="public-read",
            ContentType="text/plain",
            ServerSideEncryption="AES256",
            StorageClass="REDUCED_REDUNDANCY",
        )

        # Save the internal file before closing
        multipart = obj.initiate_multipart_upload.return_value
        multipart.parts.all.return_value = [mock.MagicMock(e_tag="123", part_number=1)]
        file.close()
        multipart.Part.assert_called_with(1)
        part = multipart.Part.return_value
        part.upload.assert_called_with(Body=content.encode("utf-8"))
        multipart.complete.assert_called_once_with(
            MultipartUpload={"Parts": [{"ETag": "123", "PartNumber": 1}]}
        )

    def test_storage_open_no_write(self):
        """
        Test opening file in write mode and closing without writing.

        A file should be created as by obj.put(...).
        """
        name = "test_open_no_write.txt"

        # Set the encryption flag used for puts
        self.storage.encryption = True
        self.storage.reduced_redundancy = True
        self.storage.default_acl = "public-read"

        file = self.storage.open(name, "w")
        self.storage.bucket.Object.assert_called_with(name)
        obj = self.storage.bucket.Object.return_value
        obj.load.side_effect = ClientError(
            {"Error": {}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "head_bucket"
        )

        # Set the name of the mock object
        obj.key = name

        # Save the internal file before closing
        file.close()

        obj.load.assert_called_once_with()
        obj.put.assert_called_once_with(
            ACL="public-read",
            Body=b"",
            ContentType="text/plain",
            ServerSideEncryption="AES256",
            StorageClass="REDUCED_REDUNDANCY",
        )

    def test_storage_open_no_overwrite_existing(self):
        """
        Test opening an existing file in write mode and closing without writing.
        """
        name = "test_open_no_overwrite_existing.txt"

        # Set the encryption flag used for puts
        self.storage.encryption = True
        self.storage.reduced_redundancy = True
        self.storage.default_acl = "public-read"

        file = self.storage.open(name, "w")
        self.storage.bucket.Object.assert_called_with(name)
        obj = self.storage.bucket.Object.return_value

        # Set the name of the mock object
        obj.key = name

        # Save the internal file before closing
        file.close()

        obj.load.assert_called_once_with()
        obj.put.assert_not_called()

    def test_storage_write_beyond_buffer_size(self):
        """
        Test writing content that exceeds the buffer size
        """
        name = "test_open_for_writïng_beyond_buffer_size.txt"

        # Set the encryption flag used for multipart uploads
        self.storage.encryption = True
        self.storage.reduced_redundancy = True
        self.storage.default_acl = "public-read"

        file = self.storage.open(name, "w")
        self.storage.bucket.Object.assert_called_with(name)
        obj = self.storage.bucket.Object.return_value
        # Set the name of the mock object
        obj.key = name

        # Initiate the multipart upload
        file.write("")
        obj.initiate_multipart_upload.assert_called_with(
            ACL="public-read",
            ContentType="text/plain",
            ServerSideEncryption="AES256",
            StorageClass="REDUCED_REDUNDANCY",
        )
        multipart = obj.initiate_multipart_upload.return_value

        # Write content at least twice as long as the buffer size
        written_content = ""
        counter = 1
        while len(written_content) < 2 * file.buffer_size:
            content = "hello, aws {counter}\n".format(counter=counter)
            # Write more than just a few bytes in each iteration to keep the
            # test reasonably fast
            content += "*" * int(file.buffer_size / 10)
            file.write(content)
            written_content += content
            counter += 1

        # Save the internal file before closing
        multipart.parts.all.return_value = [
            mock.MagicMock(e_tag="123", part_number=1),
            mock.MagicMock(e_tag="456", part_number=2),
        ]
        file.close()
        self.assertListEqual(multipart.Part.call_args_list, [mock.call(1), mock.call(2)])
        part = multipart.Part.return_value
        uploaded_content = "".join(
            args_list[1]["Body"].decode("utf-8") for args_list in part.upload.call_args_list
        )
        self.assertEqual(uploaded_content, written_content)
        multipart.complete.assert_called_once_with(
            MultipartUpload={
                "Parts": [
                    {"ETag": "123", "PartNumber": 1},
                    {"ETag": "456", "PartNumber": 2},
                ]
            }
        )

    def test_auto_creating_bucket(self):
        self.storage.auto_create_bucket = True
        Bucket = mock.MagicMock()
        self.storage._connections.connection.Bucket.return_value = Bucket
        self.storage._connections.connection.meta.client.meta.region_name = "sa-east-1"

        Bucket.meta.client.head_bucket.side_effect = ClientError(
            {"Error": {}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "head_bucket"
        )
        self.storage._get_or_create_bucket("testbucketname")
        Bucket.create.assert_called_once_with(
            ACL="public-read",
            CreateBucketConfiguration={
                "LocationConstraint": "sa-east-1",
            },
        )

    def test_auto_creating_bucket_with_acl(self):
        self.storage.auto_create_bucket = True
        self.storage.bucket_acl = "public-read"
        Bucket = mock.MagicMock()
        self.storage._connections.connection.Bucket.return_value = Bucket
        self.storage._connections.connection.meta.client.meta.region_name = "sa-east-1"

        Bucket.meta.client.head_bucket.side_effect = ClientError(
            {"Error": {}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "head_bucket"
        )
        self.storage._get_or_create_bucket("testbucketname")
        Bucket.create.assert_called_once_with(
            ACL="public-read",
            CreateBucketConfiguration={
                "LocationConstraint": "sa-east-1",
            },
        )

    def test_storage_exists(self):
        self.assertTrue(self.storage.exists("file.txt"))
        self.storage.connection.meta.client.head_object.assert_called_with(
            Bucket=self.storage.bucket_name,
            Key="file.txt",
        )

    def test_storage_exists_false(self):
        self.storage.connection.meta.client.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}},
            "HeadObject",
        )
        self.assertFalse(self.storage.exists("file.txt"))
        self.storage.connection.meta.client.head_object.assert_called_with(
            Bucket=self.storage.bucket_name,
            Key="file.txt",
        )

    def test_storage_exists_doesnt_create_bucket(self):
        with mock.patch.object(self.storage, "_get_or_create_bucket") as method:
            self.storage.exists("file.txt")
            self.assertFalse(method.called)

    def test_storage_delete(self):
        self.storage.delete("path/to/file.txt")
        self.storage.bucket.Object.assert_called_with("path/to/file.txt")
        self.storage.bucket.Object.return_value.delete.assert_called_with()

    def test_storage_listdir_base(self):
        # Files:
        #   some/path/1.txt
        #   2.txt
        #   other/path/3.txt
        #   4.txt
        pages = [
            {
                "CommonPrefixes": [
                    {"Prefix": "some"},
                    {"Prefix": "other"},
                ],
                "Contents": [
                    {"Key": "2.txt"},
                    {"Key": "4.txt"},
                ],
            },
        ]

        paginator = mock.MagicMock()
        paginator.paginate.return_value = pages
        self.storage._connections.connection.meta.client.get_paginator.return_value = paginator

        dirs, files = self.storage.listdir("")
        paginator.paginate.assert_called_with(Bucket=None, Delimiter="/", Prefix="")

        self.assertEqual(dirs, ["some", "other"])
        self.assertEqual(files, ["2.txt", "4.txt"])

    def test_storage_listdir_subdir(self):
        # Files:
        #   some/path/1.txt
        #   some/2.txt
        pages = [
            {
                "CommonPrefixes": [
                    {"Prefix": "some/path"},
                ],
                "Contents": [
                    {"Key": "some/2.txt"},
                ],
            },
        ]

        paginator = mock.MagicMock()
        paginator.paginate.return_value = pages
        self.storage._connections.connection.meta.client.get_paginator.return_value = paginator

        dirs, files = self.storage.listdir("some/")
        paginator.paginate.assert_called_with(Bucket=None, Delimiter="/", Prefix="some/")

        self.assertEqual(dirs, ["path"])
        self.assertEqual(files, ["2.txt"])

    def test_storage_size(self):
        obj = self.storage.bucket.Object.return_value
        obj.content_length = 4098

        name = "file.txt"
        self.assertEqual(self.storage.size(name), obj.content_length)

    def test_storage_mtime(self):
        # Test both USE_TZ cases
        for use_tz in (True, False):
            with self.settings(USE_TZ=use_tz):
                self._test_storage_mtime(use_tz)

    def _test_storage_mtime(self, use_tz):
        obj = self.storage.bucket.Object.return_value
        obj.last_modified = datetime.datetime.now(datetime.timezone.utc)

        name = "file.txt"
        self.assertFalse(
            is_aware(self.storage.modified_time(name)),
            "Naive datetime object expected from modified_time()",
        )

        self.assertIs(
            settings.USE_TZ,
            is_aware(self.storage.get_modified_time(name)),
            "{} datetime object expected from get_modified_time() when USE_TZ={}".format(
                ("Naive", "Aware")[settings.USE_TZ], settings.USE_TZ
            ),
        )

    def test_storage_url(self):
        name = "test_storage_size.txt"
        url = "http://aws.amazon.com/%s" % name
        self.storage.bucket.meta.client.generate_presigned_url.return_value = url
        self.storage.bucket.name = "bucket"
        self.assertEqual(self.storage.url(name), url)
        self.storage.bucket.meta.client.generate_presigned_url.assert_called_with(
            "get_object",
            Params={"Bucket": self.storage.bucket.name, "Key": name},
            ExpiresIn=self.storage.querystring_expire,
        )

        custom_expire = 123

        self.assertEqual(self.storage.url(name, expire=custom_expire), url)
        self.storage.bucket.meta.client.generate_presigned_url.assert_called_with(
            "get_object",
            Params={"Bucket": self.storage.bucket.name, "Key": name},
            ExpiresIn=custom_expire,
        )

    def test_generated_url_is_encoded(self):
        self.storage.custom_domain = "mock.cloudfront.net"
        filename = "whacky & filename.mp4"
        url = self.storage.url(filename)
        parsed_url = urlparse.urlparse(url)
        self.assertEqual(parsed_url.path, "/whacky%20%26%20filename.mp4")
        self.assertFalse(self.storage.bucket.meta.client.generate_presigned_url.called)

    def test_special_characters(self):
        self.storage.custom_domain = "mock.cloudfront.net"

        name = "ãlöhâ.jpg"
        content = ContentFile("new content")
        self.storage.save(name, content)
        self.storage.bucket.Object.assert_called_once_with(name)

        url = self.storage.url(name)
        parsed_url = urlparse.urlparse(url)
        self.assertEqual(parsed_url.path, "/%C3%A3l%C3%B6h%C3%A2.jpg")

    def test_strip_signing_parameters(self):
        expected = "http://bucket.s3-aws-region.amazonaws.com/foo/bar"
        self.assertEqual(
            self.storage._strip_signing_parameters(
                "%s?X-Amz-Date=12345678&X-Amz-Signature=Signature" % expected
            ),
            expected,
        )
        self.assertEqual(
            self.storage._strip_signing_parameters(
                "%s?expires=12345678&signature=Signature" % expected
            ),
            expected,
        )

    @skipIf(threading is None, "Test requires threading")
    def test_connection_threading(self):
        connections = []

        def thread_storage_connection():
            connections.append(self.storage.connection)

        for x in range(2):
            t = threading.Thread(target=thread_storage_connection)
            t.start()
            t.join()

        # Connection for each thread needs to be unique
        self.assertIsNot(connections[0], connections[1])

    def test_location_leading_slash(self):
        msg = (
            "S3Boto3Storage.location cannot begin with a leading slash. "
            "Found '/'. Use '' instead."
        )
        with self.assertRaises(ImproperlyConfigured, msg=msg):
            s3boto3.S3Boto3Storage(location="/")

    def test_override_class_variable(self):
        class MyStorage1(s3boto3.S3Boto3Storage):
            location = "foo1"

        storage = MyStorage1()
        self.assertEqual(storage.location, "foo1")

        class MyStorage2(s3boto3.S3Boto3Storage):
            location = "foo2"

        storage = MyStorage2()
        self.assertEqual(storage.location, "foo2")

    def test_override_init_argument(self):
        storage = s3boto3.S3Boto3Storage(location="foo1")
        self.assertEqual(storage.location, "foo1")
        storage = s3boto3.S3Boto3Storage(location="foo2")
        self.assertEqual(storage.location, "foo2")
