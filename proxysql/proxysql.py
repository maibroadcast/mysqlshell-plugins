import mysqlsh
import time
from mysqlsh import mysql

shell = mysqlsh.globals.shell


class ProxySQL:
    def __init__(self, ip, port, user, password=False):
        self.user = user
        self.ip = ip
        self.port = str(port)
        self.user = user
        self.members = []
        self.hosts = []
        self.version = None
        self.w_hostgroup = 2
        self.b_w_hostgroup = 4
        self.r_hostgroup = 3
        self.o_hostgroup = 1
        self.max_writer = 1
        self.writer_is_reader = 0
        self.max_transaction_behind = 100
        self.monitor_user = "monitor"
        self.monitor_pwd = "monitor"

        if not password:
            self.__password = shell.prompt('Password: ',{'type': 'password'})
        else:
            self.__password = password
        try:
            self.session = mysql.get_session("%s:%s@%s:%s?ssl-mode=DISABLED" % (self.user, self.__password, self.ip, self.port))
            stmt = "select version()"
            result = self.session.run_sql(stmt)
            self.version = result.fetch_one()[0]
            print("Connected to ProxySQL (%s)" % self.version)
        except:
            print("ERROR: not possible to connect to ProxySQL admin interface !")

    def __format_bytes(self, size):
        # 2**10 = 1024
        power = 2**10
        for unit in ('bytes', 'kb', 'mb', 'gb'):
            if size <= power:
                return "%d %s" % (size, unit)
            size /= power

        return "%d tb" % (size,)
    
    def __return_gr_members(self,session):
        stmt = """select member_host, member_port from performance_schema.replication_group_members"""
        result = session.run_sql(stmt)
        members = []
        members_rec = result.fetch_all()
        if len(members_rec) > 0:
            for member in members_rec:
                instance = {}
                instance['host'] = member[0]
                instance['port'] = member[1]
                members.append(instance)
            return members
        return None

    def __return_hosts(self):
        stmt = """select hostname, port from mysql_servers"""
        result = self.session.run_sql(stmt)
        hosts = []
        hosts_rec = result.fetch_all()
        if len(hosts_rec) > 0:
            for host in hosts_rec:
                instance = {}
                instance['host'] = host[0]
                instance['port'] = host[1]
                hosts.append(instance)
            return hosts
        return None

     
    def configure(self, session=None):
        if session is None:
            session = shell.get_session()
        if session is None: 
            print("No session specified. Either pass a session object to this "
                  "function or connect the shell to a member of an InnoDB Cluster")
            return
        self.members = self.__return_gr_members(session) 
        for host in self.members:
            stmt = """REPLACE INTO mysql_servers(hostgroup_id,hostname,port) 
                      VALUES (1,'%s',%d);""" % (host['host'], host['port'])
            self.session.run_sql(stmt)
        stmt = """REPLACE into mysql_group_replication_hostgroups 
                       (writer_hostgroup, backup_writer_hostgroup,
                        reader_hostgroup, offline_hostgroup, active,
                        max_writers, writer_is_also_reader, max_transactions_behind) 
                      values (%d,%d,%d,%d,1,%d,%d,%d)""" % (self.w_hostgroup,
                      self.b_w_hostgroup, self.r_hostgroup, self.o_hostgroup, 
                      self.max_writer, self.writer_is_reader, self.max_transaction_behind)
        self.session.run_sql(stmt)
        self.session.run_sql("save mysql servers to disk")
        self.session.run_sql("load mysql servers to runtime")
        self.hosts = self.__return_hosts()
        ##############################
        # configure the monitor user #
        ##############################
        # check if we have a monitor user already
        result = session.run_sql("select User from  mysql.user where User = 'monitor'")
        if len(result.fetch_all()) > 0:
            print("There is already a monitor user")
        else:
            # check is we are on primary
            result = session.run_sql("""select member_role from performance_schema.replication_group_members 
            where member_host = @@hostname and member_role='PRIMARY'""")
            if len(result.fetch_all()) == 0:
                print("ERROR: Please connect or provide a session to the Primary Master.")
                return 
            # create the monitor user in the primary
            session.run_sql("create user %s identified by '%s'" % (self.monitor_user, self.monitor_pwd))
            session.run_sql("grant select on sys.* to '%s'" % self.monitor_user)
        return    

    def get_hosts(self):
        if len(self.hosts) == 0:
            self.hosts = self.__return_hosts()
        for host in self.hosts:
            print(host)
        return
    
    def get_hostgroups(self):
        print("ProxySQL Hostgroups:")
        print("===================:")
        print ("PRIMARY   | R/W : %s" % self.w_hostgroup)
        print ("SECONDARY | R/O : %s" % self.r_hostgroup)
        print ("BACKUP PRIMARY  : %s" % self.b_w_hostgroup)
        print ("OFFLINE         : %s" % self.o_hostgroup)
        return

    def set_user_hostgroup(self, hostgroup, user, password=False):
        if not password:
            password = shell.prompt('Password: ',{'type': 'password'})
        stmt = """REPLACE into mysql_users(username, password, default_hostgroup)
                  VALUES ('%s', '%s', %d)""" % (user, password, hostgroup)
        self.session.run_sql(stmt)
        stmt = "save mysql users to disk";
        self.session.run_sql(stmt)
        stmt = "load mysql users to run";
        self.session.run_sql(stmt)
        return

    def get_user_hostgroup(self, hostgroup=False):
        if not hostgroup:
            stmt = """select username, password, default_hostgroup from mysql_users"""
        else:
            stmt = """select username, password, default_hostgroup from mysql_users
                       where default_hostgroup = %d""" % hostgroup
        result = self.session.run_sql(stmt)
        shell.dump_rows(result)
        return

    def get_version(self):
        print("Connected to ProxySQL (%s)" % self.version)
        return

    def get_status(self,loop=False):
        while True:
            stmt = """select case when hostgroup =1 THEN "OFFLINE" WHEN hostgroup=2 THEN "PRIMARY" 
                    WHEN hostgroup=3 THEN "SECONDARY" ELSE "OTHER" END HostGrpRole,Hostgroup HG, 
                    srv_host Host, 
                    srv_port Port, ConnUsed, ConnFree, ConnOK, ConnERR, MaXConnUsed, 
                    Queries, Bytes_data_sent BytesSent, Bytes_data_recv BytesRecv, Latency_us 'Latency(us)' 
                    from stats_mysql_connection_pool where status = 'ONLINE'"""
            result = self.session.run_sql(stmt)
            hosts_rec = result.fetch_all()
            print("ProxySQL Connection Pool Status")
            print("===============================")
            print("")
            print("Hostgroups:")
            print("-----------")
            print("%d : OFFLINE     %d : PRIMARY    %d : SECONDARY" % (self.o_hostgroup, self.w_hostgroup, self.r_hostgroup)) 
            if len(hosts_rec) > 0:
                result = self.session.run_sql(stmt)
                shell.dump_rows(result)
                print("Commands:")
                print("---------")
                stmt = "SELECT Command, Total_Time_us, Total_cnt FROM stats_mysql_commands_counters WHERE Total_cnt"
                result = self.session.run_sql(stmt)
                shell.dump_rows(result)
            else:
                print("ERROR: no server online currently")
            if not loop:
                return
            time.sleep(2)
            print("\033[2J")