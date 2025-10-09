from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from airflow.operators.bash import BashOperator
import pandas as pd
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

def extract_earnings_data():
    """Extract earnings data for DOW 30 companies"""
    print("Starting earnings data extraction...")

    # Sample DOW 30 companies
    dow30_symbols = [
        'AAPL', 'MSFT', 'JPM', 'V', 'JNJ', 'WMT', 'PG', 'UNH', 'HD', 'DIS',
        'MA', 'PFE', 'BAC', 'XOM', 'VZ', 'ADBE', 'KO', 'PEP', 'CMCSA', 'NFLX'
    ]

    # Create sample earnings data
    earnings_data = []
    for symbol in dow30_symbols:
        earnings_data.append({
            'symbol': symbol,
            'quarter': 'Q3 2025',
            'revenue': f"${(50 + hash(symbol) % 200):.1f}B",
            'eps': f"${(hash(symbol) % 10 + 1):.2f}",
            'extracted_at': datetime.now().isoformat()
        })

    # Save to CSV
    df = pd.DataFrame(earnings_data)
    output_path = '/tmp/dow30_earnings_raw.csv'
    df.to_csv(output_path, index=False)
    print(f"Extracted {len(earnings_data)} earnings records to {output_path}")
    return output_path

def transform_earnings_data():
    """Transform and clean the earnings data"""
    print("Starting data transformation...")

    # Read raw data
    raw_data = pd.read_csv('/tmp/dow30_earnings_raw.csv')

    # Add transformation logic
    transformed_data = raw_data.copy()
    transformed_data['revenue_billions'] = transformed_data['revenue'].str.replace('$', '').str.replace('B', '').astype(float)
    transformed_data['eps_numeric'] = transformed_data['eps'].str.replace('$', '').astype(float)
    transformed_data['market_cap_category'] = transformed_data['revenue_billions'].apply(
        lambda x: 'Large' if x > 100 else 'Medium' if x > 50 else 'Small'
    )

    # Save transformed data
    output_path = '/tmp/dow30_earnings_transformed.csv'
    transformed_data.to_csv(output_path, index=False)
    print(f"Transformed data saved to {output_path}")
    return output_path

def load_earnings_data():
    """Load the transformed data to final destination"""
    print("Starting data loading...")

    # Read transformed data
    final_data = pd.read_csv('/tmp/dow30_earnings_transformed.csv')

    # Create final output directory
    final_output_dir = '/tmp/airflow_output'
    os.makedirs(final_output_dir, exist_ok=True)

    # Save final data with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    final_path = f"{final_output_dir}/dow30_earnings_final_{timestamp}.csv"
    final_data.to_csv(final_path, index=False)

    print(f"Final data loaded to {final_path}")
    print(f"Total records processed: {len(final_data)}")
    return final_path

# Define the DAG
dag = DAG(
    'dow30_earnings_pipeline',
    default_args=default_args,
    description='DOW 30 Earnings Intelligence Pipeline',
    schedule=timedelta(days=1),
    catchup=False,
    tags=['dow30', 'earnings', 'etl'],
)

# Define tasks
start_task = BashOperator(
    task_id='start_pipeline',
    bash_command='echo "Starting DOW 30 Earnings Pipeline..."',
    dag=dag,
)

extract_task = PythonOperator(
    task_id='extract_earnings_data',
    python_callable=extract_earnings_data,
    dag=dag,
)

transform_task = PythonOperator(
    task_id='transform_earnings_data',
    python_callable=transform_earnings_data,
    dag=dag,
)

load_task = PythonOperator(
    task_id='load_earnings_data',
    python_callable=load_earnings_data,
    dag=dag,
)

end_task = BashOperator(
    task_id='end_pipeline',
    bash_command='echo "DOW 30 Earnings Pipeline completed successfully!"',
    dag=dag,
)

# Define task dependencies
start_task >> extract_task >> transform_task >> load_task >> end_task