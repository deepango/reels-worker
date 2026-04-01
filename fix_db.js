const { Client } = require('pg');

const connectionString = process.env.DATABASE_URL;
if (!connectionString) {
  throw new Error('Missing DATABASE_URL environment variable');
}

const client = new Client({
  connectionString,
  ssl: { rejectUnauthorized: false },
});

client
  .connect()
  .then(() =>
    client.query(
      "INSERT INTO customers (id, email) VALUES (0, 'test0@example.com'), (1, 'test1@example.com') ON CONFLICT (id) DO NOTHING"
    )
  )
  .then(() => {
    console.log('Inserted customer 0 and 1');
    process.exit(0);
  })
  .catch((err) => {
    console.error(err);
    process.exit(1);
  });
