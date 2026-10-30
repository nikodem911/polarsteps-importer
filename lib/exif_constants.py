"""
Shared EXIF tag constants used for reading image metadata.
Tag IDs are from the EXIF 2.3 specification.
"""

# Root IFD date/time tags
EXIF_TAG_DATETIME = 306               # DateTime

# Exif SubIFD date/time tags
EXIF_TAG_DATETIME_ORIGINAL  = 36867   # DateTimeOriginal
EXIF_TAG_DATETIME_DIGITIZED = 36868   # DateTimeDigitized
EXIF_TAG_OFFSET_TIME        = 36880   # OffsetTime
EXIF_TAG_OFFSET_TIME_ORIG   = 36881   # OffsetTimeOriginal

# IFD pointers
EXIF_IFD_EXIF = 0x8769   # Exif SubIFD pointer
EXIF_IFD_GPS  = 0x8825   # GPS IFD pointer

# GPS sub-tags (within GPS IFD)
GPS_TAG_LAT_REF = 1
GPS_TAG_LAT     = 2
GPS_TAG_LON_REF = 3
GPS_TAG_LON     = 4

