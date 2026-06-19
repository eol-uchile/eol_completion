# Python Standard Libraries
from itertools import islice

# Installed packages (via pip)
from django.db import connection
import numpy as np

# Edx dependencies
from lms.djangoapps.certificates.models import GeneratedCertificate
from opaque_keys.edx.keys import UsageKey

def get_certificates(students_id, course_key):
    """
        Return ('Si'/'No', 0/1) certificate indicators for given students in a course.
    """
    certificates = GeneratedCertificate.objects.filter(status='downloadable', user_id__in=students_id, course_id=course_key).values_list("user_id", flat=True)
    cert_flags = np.isin(students_id, list(set(certificates))).view(np.uint8)
    cert_strs  = np.where(cert_flags, 'Si', 'No')
    return cert_strs, cert_flags
        
def get_units(info, not_completable_blocks=('discussion+block', 'eoldiscussion+block')):
    """Extract unit requirements and section sizes from a course structure.

        Returns:
        - unit_reqs: list of tuples of UsageKeys for completable blocks in each unit
        - section_sizes: number of non-empty units per section

        Process `info` (info = Content().dump_module(store.get_course(course_key))) to identify required blocks per unit and section.

        Hierarchy: course -> chapter(section) -> sequential(subsection) -> vertical(unit)
    """
    course_root = next((v for v in info.values() if v.get('category') == 'course'), None)
    if not course_root:
        return [], []
    unit_reqs = []
    section_sizes = []
    for section_id in course_root.get('children', []):
        section = info.get(section_id, {})
        curr_sec = 0
        for subsection_id in section.get('children', []):
            subsection = info.get(subsection_id, {})
            for unit_id in subsection.get('children', []):
                unit = info.get(unit_id, {})
                children = unit.get('children', [])
                if children:
                    # The blocks of type `discussion+block` y `eoldiscussion+block` are discarded, as in https://github.com/eol-uchile/eol_completion/blob/8d90f07eb0f2ffa639007f9893c8876fd6f8441f/eol_completion/views.py#L487
                    unit_reqs.append(tuple(UsageKey.from_string(b) for b in children if all(x not in b for x in not_completable_blocks)))
                    curr_sec += 1
        if curr_sec > 0:
            section_sizes.append(curr_sec)
    return unit_reqs, section_sizes
        
def chunked(iterable, size):
    """
    Yield successive lists of length size from iterable.
    """
    it = iter(iterable)
    while chunk := list(islice(it, size)):
        yield chunk
            
def build_completion_matrix(user_ids, block_keys, user_idx_map, block_idx_map_keys, context_key, chunk_size=1000):
    """
    Builds the N x B completion matrix by streaming DB results directly into
    the matrix - never accumulating all rows in memory at once.
    """
    COMPLETION_TABLE_NAME = 'completion_blockcompletion'
    N = len(user_ids)
    B = len(block_keys)
    C = np.zeros((N, B), dtype=np.uint8)
    block_placeholders = ','.join(['%s'] * len(block_keys))
    for user_chunk in chunked(user_ids, chunk_size):
        user_placeholders = ','.join(['%s'] * len(user_chunk))
        query = f"""
            SELECT user_id, block_key FROM {COMPLETION_TABLE_NAME} WHERE course_key = %s AND completion = 1.0 
            AND user_id IN ({user_placeholders}) AND block_key IN ({block_placeholders})
        """
        params = [str(context_key)] + user_chunk + block_keys

        with connection.cursor() as cursor:
            cursor.execute(query, params)
            # write directly into matrix C
            row_idx, col_idx = [], []
            # iterate the cursor
            for uid, bk in cursor:
                if uid in user_idx_map and bk in block_idx_map_keys:
                    row_idx.append(user_idx_map[uid])
                    col_idx.append(block_idx_map_keys[bk])
            if row_idx:
                C[row_idx, col_idx] = 1
    return C
    