# -*- coding: utf-8 -*-

# Python Standard Libraries
from collections import OrderedDict
from itertools import islice
import logging
import six

# Installed packages (via pip)
from django.contrib.auth.models import User
from django.db import connection
from django.db.models import Max
from eol_sso.services.interface import get_user_id_with_indiv_id_list
import numpy as np

# Edx dependencies
from common.djangoapps.student.models import CourseAccessRole
from completion.models import BlockCompletion
from lms.djangoapps.certificates.models import GeneratedCertificate
from opaque_keys.edx.keys import UsageKey, LearningContextKey
from xblock_discussion import DiscussionXBlock
from xblock.fields import Scope
from xmodule.modulestore.inheritance import own_metadata

logger = logging.getLogger(__name__)
FILTER_LIST = ['xml_attributes']
INHERITED_FILTER_LIST = ['children', 'xml_attributes']
LIMIT_STUDENTS = 10000

class Content(object):
    def get_content(self, info, id_course):
        """
            Returns dictionary of ordered sections, subsections and units
        """
        max_unit = 0   # Number of units in all sections
        content = OrderedDict()
        children_course = info[id_course]
        children_course = children_course['children']  # All course sections
        children = 0  # Number of units per section
        for id_section in children_course:  # Iterate each section
            section = info[id_section]
            aux_name_sec = section['metadata']
            children = 0
            content[id_section] = {
                'type': 'section',
                'name': aux_name_sec['display_name'],
                'id': id_section,
                'num_children': children}
            subsections = section['children']
            for id_subsection in subsections:  # Iterate each subsection
                subsection = info[id_subsection]
                units = subsection['children']
                aux_name = subsection['metadata']
                len_unit = len(units)
                content[id_subsection] = {
                    'type': 'subsection',
                    'name': aux_name['display_name'],
                    'id': id_subsection,
                    'num_children': 0}
                for id_uni in units:  # Iterate each unit and get unit name
                    unit = info[id_uni]
                    if len(unit['children']) > 0:
                        max_unit += 1
                        content[id_uni] = {
                            'type': 'unit',
                            'name': unit['metadata']['display_name'],
                            'id': id_uni}
                    else:
                        len_unit -= 1
                children += len_unit
                content[id_subsection]['num_children'] = len_unit
            content[id_section] = {
                'type': 'section',
                'name': aux_name_sec['display_name'],
                'id': id_section,
                'num_children': children}

        return content, max_unit

    def dump_module(
            self,
            module,
            destination=None,
            inherited=False,
            defaults=False):
        """
        Add the module and all its children to the destination dictionary in
        as a flat structure.
        """

        destination = destination if destination else {}

        items = own_metadata(module)

        # HACK: add discussion ids to list of items to export (AN-6696)
        if isinstance(
                module,
                DiscussionXBlock) and 'discussion_id' not in items:
            items['discussion_id'] = module.discussion_id

        filtered_metadata = {
            k: v for k,
            v in six.iteritems(items) if k not in FILTER_LIST}

        destination[six.text_type(module.location)] = {
            'category': module.location.block_type,
            'children': [six.text_type(child) for child in getattr(module, 'children', [])],
            'metadata': filtered_metadata,
        }

        if inherited:
            # When calculating inherited metadata, don't include existing
            # locally-defined metadata
            inherited_metadata_filter_list = list(filtered_metadata.keys())
            inherited_metadata_filter_list.extend(INHERITED_FILTER_LIST)

            def is_inherited(field):
                if field.name in inherited_metadata_filter_list:
                    return False
                elif field.scope != Scope.settings:
                    return False
                elif defaults:
                    return True
                else:
                    return field.values != field.default

            inherited_metadata = {field.name: field.read_json(
                module) for field in list(module.fields.values()) if is_inherited(field)}
            destination[six.text_type(
                module.location)]['inherited_metadata'] = inherited_metadata

        for child in module.get_children():
            self.dump_module(child, destination, inherited, defaults)

        return destination

def get_certificates(students_id, course_key):
    """
    Return ('Si'/'No', 0/1) certificate indicators for given students in a course.
    """
    certificates = GeneratedCertificate.objects.filter(status='downloadable', user_id__in=students_id, course_id=course_key).values_list("user_id", flat=True)
    cert_flags = np.isin(students_id, list(set(certificates))).view(np.uint8)
    cert_strs = np.where(cert_flags, 'Si', 'No')
    return cert_strs, cert_flags
            
def get_units( info, not_completable_blocks=('discussion+block', 'eoldiscussion+block', 'invideoquiz+block')):
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
                    # The blocks of type `discussion+block` y `eoldiscussion+block` are discarded as they are non completable blocks
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
    Builds the N x B completion matrix by streaming DB results directly into the matrix, never accumulating all rows in memory at once.
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

def get_ticks(
        content,
        info,
        enrolled_students,
        course_key,
        max_unit):
    """
        Dictionary of students with ticks if students completed the units
    """
    # Do not process the course in case you have 0 students enrolled
    if not enrolled_students:
        return {'data': [[True]], 'completion': []}
    
    students_id, students_username, students_email, students_indiv_id = map(list, zip(*((x['id'], x['username'], x['email'], x['indiv_id']) for x in enrolled_students)))
    cert_strs, cert_flags = get_certificates(students_id, course_key) # 
    unit_reqs, section_sizes = get_units(info) #
    unique_blocks = [item for sublist in unit_reqs for item in sublist] # All course completable blocks in the course, concatenated by unit
    
    N = len(students_id)    # Number of students
    B = len(unique_blocks)  # Number of completable blocks in the course
    U = len(unit_reqs)      # Number of units in the course (verticals)

    if B > 0:
        user_idx_map = {uid: i for i, uid in enumerate(students_id)}
        user_ids = [int(uid) for uid in students_id]

        block_idx_map = {b: k for k, b in enumerate(unique_blocks)}
        block_keys = [str(k) for k in block_idx_map.keys()]
        block_idx_map_keys = {b: k for k, b in enumerate(block_keys)} # Gives an additional numerical order to the blocks
        
        # Fetch all matching block completions and populate the C matrix
        context_key = LearningContextKey.from_string(str(course_key))

        # Build a completion matrix C
        C = build_completion_matrix(user_ids, block_keys, user_idx_map, block_idx_map_keys, context_key, chunk_size=1000)

        # Construct a matrix M (B blocks x U units)
        M = np.zeros((B, U), dtype=np.uint8)
        if unit_reqs:
            pairs = np.array([(block_idx_map[b], j) for j, req in enumerate(unit_reqs) for b in req], dtype=np.intp,)
            if pairs.size:
                M[pairs[:, 0], pairs[:, 1]] = 1

        # Compute Unit completions as a matrix product C * M
        # If student completed all required blocks for a unit, the product equals the block count. 
        # Then to determine if a unit is completed, the number of completed blocks is compared with the number of completable blocks in that unit.
        # UC (N x U) = C (N x B) * M (B x U) == M.sum (U)
        unit_completions = (C @ M == M.sum(axis=0)).astype(np.bool_)
    else:
        # If no blocks are required, assume all units are implicitly completed
        unit_completions = np.ones((N, U), dtype=np.bool_)
    
    TICK = '&#10004;'
    cols = []
    completion_acc = []
    
    # Aggregate unit completions per section as fractions (e.g. '3/4')
    start_u = 0
    for size in section_sizes:
        if size <= 0:
            continue
        end_u = start_u + size
        sec_units = unit_completions[:, start_u:end_u]
        unit_strs = np.where(sec_units == 1, TICK, '')
        for j in range(size):
            cols.append(unit_strs[:, j])
            completion_acc.append(np.sum(sec_units[:, j]))
        sec_sum = np.sum(sec_units, axis=1)
        cols.append(np.array([f"{v}/{size}" for v in sec_sum], dtype=object))
        sec_done_flags = (sec_sum == size).astype(int)
        completion_acc.append(np.sum(sec_done_flags))
        start_u = end_u
        
    # Aggregate global total fraction of completions for the entire course (e.g. '12/12')
    total_sum = np.sum(unit_completions, axis=1)
    total_strs = np.char.add(total_sum.astype(str), f"/{max_unit}")
    cols.append(total_strs)
    
    if max_unit > 0:
        total_done_flags = (total_sum == max_unit).astype(int)
    else:
        total_done_flags = np.zeros(N, dtype=int)
        
    completion_acc.append(np.sum(total_done_flags))
    completion_acc.append(np.sum(cert_flags))
    
    # Horizontally stack student metadata with computed columns into a final dictionary
    indiv_ids = np.array([x if x is not None else '' for x in students_indiv_id], dtype=object)
    usernames = np.array(students_username, dtype=object)
    emails = np.array(students_email, dtype=object)
    
    data_matrix = np.column_stack([emails, usernames, indiv_ids] + cols + [cert_strs])
    user_tick = {
        'data': data_matrix.tolist(),
        'completion': [str(x) for x in completion_acc]
    }
    return user_tick

def get_context_big_course( course_key):
    """
        Return eol completion data
    """
    context_key = LearningContextKey.from_string(str(course_key))
    aux_block_completions = BlockCompletion.objects.filter(context_key=context_key).values('user').annotate(last_completed=Max('modified'))
    last_block_completions= {}
    for x in aux_block_completions:
        last_block_completions[x['user']] = x['last_completed']
    staff_query = CourseAccessRole.objects.filter(
        course_id=course_key
    ).values('user_id')
    enrolled_students = User.objects.filter(
        courseenrollment__course_id=course_key,
        courseenrollment__is_active=1
    ).exclude(
        id__in=staff_query
    ).order_by('username').values('id', 'username', 'email', 'last_login')
    user_id_list = enrolled_students.values_list('id', flat=True)
    user_indiv_id_list = get_user_id_with_indiv_id_list(user_id_list)
    if user_indiv_id_list != []:
        user_indiv_id_dict = {id: indiv_id for id, indiv_id in user_indiv_id_list}
        for user in enrolled_students:
            indiv_id = user_indiv_id_dict.get(user['id'], '')
            user['indiv_id'] = indiv_id
    context = [
        [x['username'], 
        x['indiv_id'], 
        x['email'], 
        last_block_completions[x['id']].strftime("%d/%m/%Y, %H:%M:%S") if x['id'] in last_block_completions else '', 
        x['last_login'].strftime("%d/%m/%Y, %H:%M:%S") if x['last_login'] else ''] for x in enrolled_students
    ]
    if len(context) == 0:
        context = [[True]]
    return {'data': context}
