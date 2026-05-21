const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

// Intentional syntax error - missing closing parenthesis
app.get('/', function(req, res {
  res.send('Hello World!');
});

app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
