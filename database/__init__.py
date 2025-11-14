"""
Database module for SGLang CI Dashboard

Provides persistent storage for test results, logs, and plots with GitHub sync.
"""

from database.database import DashboardDatabase
from database.db_data_collector import DatabaseDataCollector

__all__ = ["DashboardDatabase", "DatabaseDataCollector"]
