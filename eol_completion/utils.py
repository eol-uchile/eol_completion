# Edx dependencies
from opaque_keys.edx.keys import UsageKey, LearningContextKey

# Installed packages (via pip)
from django.db.models import Count, Q
import numpy as np

# Internal project dependencies
from completion.models import BlockCompletion

def get_units(info, not_completable_blocks=('discussion+block', 'eoldiscussion+block')):
    """
    Obtain total of blocks and section sizes
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
                    unit_reqs.append(tuple(UsageKey.from_string(b) for b in children if all(x not in b for x in not_completable_blocks)))
                    curr_sec += 1
        if curr_sec > 0:
            section_sizes.append(curr_sec)
    return unit_reqs, section_sizes
    
def get_completed_percentage_per_user(info, enrolled_students, course_key):
    """
    Total of completed xblocks in percentage by user
    """
    unit_reqs, _ = get_units(info)
    context_key = LearningContextKey.from_string(str(course_key))
    students_id = map(list, zip(*((x['id']) for x in enrolled_students))) 
    user_idx_map = {uid: i for i, uid in enumerate(students_id)}  
    unique_blocks = [item for sublist in unit_reqs for item in sublist] # All course completable blocks in the course, concatenated by unit
    block_idx_map = {b: k for k, b in enumerate(unique_blocks)}
    N = len(students_id)    # Number of students
    B = len(unique_blocks)  # Number of completable blocks in the course
    C_1d = np.zeros(N, dtype=np.uint8)
    qs2 = BlockCompletion.objects.filter(user_id__in=students_id, context_key=context_key, completion=1.0, block_key__in=block_idx_map).exclude(Q(block_key__icontains='discussion') | Q(block_key__icontains='eoldiscussion')).values('user_id').annotate(block_count=Count('block_key'))
    for row in qs2.iterator(chunk_size=100000):
        uid = row['user_id']
        if uid in user_idx_map:
            C_1d[user_idx_map[uid]] = row['block_count']
    return C_1d/B
