
# LDAP server configuration
LDAP_SERVER = 'tdslicerhub-openldap.tsliceh.svc.cluster.local:389'
BIND_USER = 'cn=admin,dc=opendx,dc=org'
BIND_PASSWORD = 'admin_pass'
BASE_DN = 'dc=opendx,dc=org'


import pandas as pd
from ldap3 import Server, Connection, MODIFY_ADD, SUBTREE, ALL_ATTRIBUTES, MODIFY_REPLACE
from ldap3.core.exceptions import LDAPException



def connect():
    server = Server(LDAP_SERVER)
    conn = Connection(server, user=BIND_USER, password=BIND_PASSWORD, auto_bind=True)
    return conn


def check_credentials(user, password, ou='slicerhub'):
    try:
        with Connection(LDAP_SERVER, user=f"uid={user},ou={ou},{BASE_DN}", password=password,
                        read_only=True) as conn:
            print(conn.result["description"])  # "success" if bind is ok
            return True
    except LDAPException as e:
        print(e)


def add_users_csv(conn, csv_file):
    # Iterate through the DataFrame and create LDAP entries
    df = pd.read_csv(csv_file)
    for index, row in df.iterrows():
        uid = row['UID']
        cn = row['Common Name']
        sn = row['Surname']
        password = row['Password']
        org_unit = row['ortganization unit']

        # Define the LDAP entry
        entry = {
            'objectClass': ['top', 'person', 'organizationalPerson', 'inetOrgPerson'],
            'cn': cn,
            'sn': sn,
            'uid': uid,
            'userPassword': password  # Include the hashed password
        }

        # Define the DN (Distinguished Name)
        dn = f'uid={uid},ou={org_unit},{BASE_DN}'  # Adjust the DN structure as needed

        if conn.search(dn, '(objectClass=inetOrgPerson)'):
            # User already exists, you can choose to update the user's attributes or raise an error
            # Example: Update user's attributes
            print(f"user {dn} already exists")
            print(f"password will be updated")
            conn.modify(dn, {'userPassword': [(MODIFY_REPLACE, [password])]})
            print(print(conn.result["description"]))

        else:
            # User doesn't exist, create a new entry
            conn.add(dn, attributes=entry)
            print(conn.result["description"])

    conn.unbind()


def get_all_ldap_users(conn):
    try:
        # Connect to the LDAP server
        server = Server(LDAP_SERVER)
        conn = Connection(server, user=BIND_USER, password=BIND_PASSWORD, auto_bind=True)

        # Search for all user entries in the LDAP directory
        conn.search(BASE_DN, '(objectClass=inetOrgPerson)', search_scope=SUBTREE, attributes=ALL_ATTRIBUTES)

        # Retrieve and print the user entries
        users = conn.entries
        for user in users:
            print(user.entry_dn)  # Print the DN of each user entry

    except Exception as e:
        print(f'Error retrieving users: {str(e)}')

    finally:
        # Disconnect from the LDAP server
        conn.unbind()


def delete_ldap_user(conn, user_uid, org_unit):
    user_dn = f'uid={user_uid},ou={org_unit},{BASE_DN}'
    try:
        # Connect to the LDAP server
        server = Server(LDAP_SERVER)
        conn = Connection(server, user=BIND_USER, password=BIND_PASSWORD, auto_bind=True)

        # Delete the user entry
        conn.delete(user_dn)
        print(conn.result["description"])

    except Exception as e:
        print(f'Error deleting user: {str(e)}')

    finally:
        # Disconnect from the LDAP server
        conn.unbind()


def create_ldap_ou(ou_dn, ou_name):
    # ou_db = "ou=new_org_unit,dc=opendx,dc=org"
    # ou_name = new_org_unit_name (que puede ser igual
    try:
        # Connect to the LDAP server
        server = Server(LDAP_SERVER)
        conn = Connection(server, user=BIND_USER, password=BIND_PASSWORD, auto_bind=True)

        # Define the attributes for the new organizational unit
        attributes = {
            'objectClass': ['top', 'organizationalUnit'],
            'ou': ou_name
        }

        # Create the new organizational unit entry
        conn.add(ou_dn, attributes=attributes)
        print(conn.result["description"])

    except Exception as e:
        print(f'Error creating OU: {str(e)}')

    finally:
        # Disconnect from the LDAP server
        conn.unbind()


