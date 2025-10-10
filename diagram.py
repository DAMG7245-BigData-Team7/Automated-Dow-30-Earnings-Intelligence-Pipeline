# diagram.py
from diagrams import Diagram, Cluster, Edge
from diagrams.aws.compute import EC2
from diagrams.aws.database import RDS
from diagrams.aws.storage import S3
from diagrams.aws.analytics import Glue
from diagrams.onprem.workflow import Airflow
from diagrams.onprem.client import Users
from diagrams.programming.language import Python
from diagrams.aws.security import IAM

with Diagram("Dow 30 Earnings Intelligence Pipeline", show=False, direction="TB"):
    
    # Data Sources
    users = Users("Dow 30 IR Sites")
    
    with Cluster("Data Collection Layer"):
        with Cluster("Web Scraping"):
            ir_extractor = Python("IR Extractor")
            file_downloader = Python("File Downloader")
            
    with Cluster("Processing Layer"):
        with Cluster("Document Processing"):
            docling = Python("Docling Parser")
            
    with Cluster("Orchestration"):
        airflow = Airflow("Apache Airflow")
        
    with Cluster("AWS Infrastructure"):
        with Cluster("Storage"):
            s3_raw = S3("raw-documents")
            s3_parsed = S3("earnings-documents")
            s3_bucket = S3("doc-dow-30-2025")
            
        with Cluster("Database"):
            rds = RDS("PostgreSQL\n(Metadata)")
            
    # Data flow
    users >> Edge(label="HTML") >> ir_extractor
    ir_extractor >> Edge(label="IR URLs") >> file_downloader
    file_downloader >> Edge(label="PDFs") >> s3_raw
    s3_raw >> Edge(label="Raw Docs") >> docling
    docling >> Edge(label="Parsed Content") >> s3_parsed
    
    # S3 consolidation
    s3_raw >> s3_bucket
    s3_parsed >> s3_bucket
    
    # Metadata flow
    s3_bucket >> Edge(label="Metadata") >> rds
    
    # Orchestration
    airflow >> Edge(style="dashed", label="triggers") >> ir_extractor
    airflow >> Edge(style="dashed", label="triggers") >> file_downloader
    airflow >> Edge(style="dashed", label="triggers") >> docling