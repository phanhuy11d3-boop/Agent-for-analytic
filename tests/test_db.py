from tools.sql_executor import execute_sql

r = execute_sql("SELECT COUNT(*) as total FROM retail_fraud_transactions")
print("Row count:", r)

r2 = execute_sql(
    "SELECT payment_method, ROUND(AVG(fraud_flag)*100,2) as fraud_rate "
    "FROM retail_fraud_transactions "
    "GROUP BY payment_method ORDER BY fraud_rate DESC"
)
print("\nFraud rate by payment method:")
for row in r2.get("data", []):
    print(" ", row)
