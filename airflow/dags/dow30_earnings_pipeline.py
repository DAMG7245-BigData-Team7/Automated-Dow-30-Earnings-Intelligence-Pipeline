from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator
import os

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'start_date': datetime(2025, 10, 8),
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

# Define the DAG
dag = DAG(
    'dow30_earnings_pipeline',
    default_args=default_args,
    description='DOW 30 Earnings Intelligence Pipeline',
    schedule=timedelta(days=1),
    catchup=False,
    tags=['dow30', 'earnings', 'intelligence'],
)

# Get the directory containing the DAG file
dag_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(os.path.dirname(dag_dir))

# Define tasks
start_task = BashOperator(
    task_id='start_pipeline',
    bash_command='echo "Starting DOW 30 Earnings Intelligence Pipeline..."',
    dag=dag,
)

ir_extractor_task = BashOperator(
    task_id='ir_extractor',
    bash_command=f'cd "{project_root}" && python3 IR_extractor.py',
    dag=dag,
)

file_downloader_task = BashOperator(
    task_id='file_downloader',
    bash_command=f'cd "{project_root}" && python3 File_Downloader_v2.py',
    dag=dag,
)

docling_parser_task = BashOperator(
    task_id='docling_parser',
    bash_command=f'cd "{project_root}" && python3 docling_parser.py --downloads data/raw --output data/parsed',
    dag=dag,
)

upload_s3_task = BashOperator(
    task_id='upload_s3',
    bash_command=f'cd "{project_root}" && python3 s3_upload.py --bucket doc-dow-30-2025 --parsed-root data/parsed --ticker AXP'
)

end_task = BashOperator(
    task_id='end_pipeline',
    bash_command='echo "DOW 30 Earnings Intelligence Pipeline completed successfully!"',
    dag=dag,
)



# Define task dependencies
start_task >> ir_extractor_task >> file_downloader_task >> docling_parser_task >> upload_s3_task >> end_task