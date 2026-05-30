from .gmail_parser import (
    validate_credentials,
    build_gmail_service,
    get_email_ids,
    get_email_items_main,
    mark_messages_as_read,
    # Legacy alias — prefer get_email_ids()
    get_email_Ids,
)
