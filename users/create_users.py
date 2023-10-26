import pandas as pd
import hashlib
from ldap3 import Server, Connection, MODIFY_ADD

# LDAP server configuration
ldap_server = 'ldap://your_ldap_server'
ldap_admin_dn = 'cn=admin,dc=example,dc=com'
ldap_admin_password = 'admin_password'

# Read the Excel sheet
excel_file = 'student_data.xlsx'  # Update with your Excel file path
df = pd.read_excel(excel_file)

# LDAP connection setup
server = Server(ldap_server)
conn = Connection(server, user=ldap_admin_dn, password=ldap_admin_password, auto_bind=True)

# Iterate through the DataFrame and create LDAP entries
for index, row in df.iterrows():
    uid = row['UID']
    cn = row['Common Name']
    sn = row['Surname']
    plain_password = row['Password']

    # Hash the password (you can use a different hashing algorithm if needed)
    hashed_password = hashlib.sha256(plain_password.encode()).hexdigest()

    # Define the LDAP entry
    entry = {
        'objectClass': ['top', 'person', 'organizationalPerson', 'inetOrgPerson'],
        'cn': cn,
        'sn': sn,
        'uid': uid,
        'userPassword': hashed_password  # Include the hashed password
    }

    # Define the DN (Distinguished Name)
    dn = f'uid={uid},ou=people,dc=example,dc=com'  # Adjust the DN structure as needed

    # Add the entry to LDAP
    conn.add(dn, attributes=entry)

# Close the LDAP connection
conn.unbind()
