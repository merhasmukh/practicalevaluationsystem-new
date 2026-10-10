"""add_pdf_header_and_signature

Revision ID: 917ce21a52bd
Revises: 34c799183917
Create Date: 2026-10-10 13:14:39.723486

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '917ce21a52bd'
down_revision: Union[str, Sequence[str], None] = '34c799183917'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "subject_header_configs" not in tables:
        op.create_table(
            'subject_header_configs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('subject_id', sa.Integer(), nullable=False),
            sa.Column('institution_name', sa.String(length=200), server_default='Gujarat Vidyapith', nullable=False),
            sa.Column('department_name', sa.String(length=200), server_default='Department of Computer Science', nullable=False),
            sa.Column('course_name', sa.String(length=150), server_default='', nullable=False),
            sa.Column('academic_year', sa.String(length=50), server_default='2026-27', nullable=False),
            sa.Column('subject_teacher', sa.String(length=150), server_default='', nullable=False),
            sa.Column('subject_title', sa.String(length=300), server_default='', nullable=False),
            sa.Column('signature_image_path', sa.String(length=255), nullable=True),
            sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(['subject_id'], ['subjects.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('subject_id')
        )
    else:
        cfg_cols = [c["name"] for c in insp.get_columns("subject_header_configs")]
        with op.batch_alter_table('subject_header_configs', schema=None) as batch_op:
            if 'institution_name' not in cfg_cols:
                batch_op.add_column(sa.Column('institution_name', sa.String(length=200), server_default='Gujarat Vidyapith', nullable=False))
            if 'department_name' not in cfg_cols:
                batch_op.add_column(sa.Column('department_name', sa.String(length=200), server_default='Department of Computer Science', nullable=False))
            if 'course_name' not in cfg_cols:
                batch_op.add_column(sa.Column('course_name', sa.String(length=150), server_default='', nullable=False))
            if 'academic_year' not in cfg_cols:
                batch_op.add_column(sa.Column('academic_year', sa.String(length=50), server_default='2026-27', nullable=False))
            if 'subject_teacher' not in cfg_cols:
                batch_op.add_column(sa.Column('subject_teacher', sa.String(length=150), server_default='', nullable=False))
            if 'subject_title' not in cfg_cols:
                batch_op.add_column(sa.Column('subject_title', sa.String(length=300), server_default='', nullable=False))
            if 'signature_image_path' not in cfg_cols:
                batch_op.add_column(sa.Column('signature_image_path', sa.String(length=255), nullable=True))
            if 'updated_at' not in cfg_cols:
                batch_op.add_column(sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now(), nullable=False))

    if "practicals" in tables:
        practical_cols = [c["name"] for c in insp.get_columns("practicals")]
        with op.batch_alter_table('practicals', schema=None) as batch_op:
            if 'unit_name' not in practical_cols:
                batch_op.add_column(sa.Column('unit_name', sa.String(length=100), nullable=True))
            if 'practical_code' not in practical_cols:
                batch_op.add_column(sa.Column('practical_code', sa.String(length=30), nullable=True))
            if 'assigned_date' not in practical_cols:
                batch_op.add_column(sa.Column('assigned_date', sa.Date(), nullable=True))

    if "users" in tables:
        user_cols = [c["name"] for c in insp.get_columns("users")]
        with op.batch_alter_table('users', schema=None) as batch_op:
            if 'signature_image_path' not in user_cols:
                batch_op.add_column(sa.Column('signature_image_path', sa.String(length=255), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = insp.get_table_names()

    if "users" in tables:
        user_cols = [c["name"] for c in insp.get_columns("users")]
        with op.batch_alter_table('users', schema=None) as batch_op:
            if 'signature_image_path' in user_cols:
                batch_op.drop_column('signature_image_path')

    if "practicals" in tables:
        practical_cols = [c["name"] for c in insp.get_columns("practicals")]
        with op.batch_alter_table('practicals', schema=None) as batch_op:
            if 'assigned_date' in practical_cols:
                batch_op.drop_column('assigned_date')
            if 'practical_code' in practical_cols:
                batch_op.drop_column('practical_code')
            if 'unit_name' in practical_cols:
                batch_op.drop_column('unit_name')

    if "subject_header_configs" in tables:
        op.drop_table('subject_header_configs')
