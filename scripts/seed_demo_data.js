// Run with a setup user; give the extension user the read role on this database.
const orders = [
  ["PENDING", 125.50, "Alice", "eu-north-1", 120],
  ["PENDING", 89.00, "Bob", "eu-north-1", 60],
  ["SUCCESS", 250.00, "Carol", "eu-west-1", 180],
  ["SUCCESS", 175.25, "Dave", "eu-west-1", 45],
  ["FAILED", 42.99, "Eve", "us-east-1", 30],
  ["FAILED", 19.50, "Frank", "us-east-1", 15],
  ["PENDING", 310.00, "Grace", "eu-north-1", 0],
  ["SUCCESS", 520.75, "Henry", "eu-west-1", 0],
  ["FAILED", 67.40, "Ivy", "us-east-1", 0],
  ["PENDING", 144.20, "Jack", "eu-north-1", 0],
  ["SUCCESS", 999.99, "Kate", "ap-south-1", 0]
];
db.dt_demo_orders.insertMany(orders.map((row, i) => ({
  id: i + 1, status: row[0], amount: row[1], customer_name: row[2],
  region: row[3], created_at: new Date(Date.now() - row[4] * 60000)
})));
