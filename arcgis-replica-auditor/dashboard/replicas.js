// SAMPLE DATA - the audit script overwrites this file on every run.
var REPLICA_GENERATED = "2026-07-15T06:00:00";
var REPLICA_DATABASES = ["mygdb1", "mygdb2"];
var REPLICA_DATA = [
  {
    "replicaId": "A1B2C3D4-0000-0000-0000-000000000001",
    "name": "DBO.Ags_Fs_1",
    "owner": "field.user1@example.com",
    "sourceDatabase": "mygdb1",
    "serviceUrl": "https://gis.example.com/arcgis/rest/services/Collector/FieldMapsLayers/FeatureServer",
    "serviceName": "FieldMapsLayers",
    "creationDate": "2026-06-20T14:02:11+00:00",
    "lastSyncDate": "2026-07-14T16:41:03+00:00",
    "syncModel": "perLayer",
    "syncDirection": "bidirectional",
    "sdeType": "TwoWay",
    "sdeRole": "Parent",
    "hasConflicts": false
  },
  {
    "replicaId": "A1B2C3D4-0000-0000-0000-000000000002",
    "name": "DBO.Ags_Fs_2",
    "owner": "field.user2@example.com",
    "sourceDatabase": "mygdb1",
    "serviceUrl": "https://gis.example.com/arcgis/rest/services/Collector/Inspections/FeatureServer",
    "serviceName": "Inspections",
    "creationDate": "2026-05-30T09:12:45+00:00",
    "lastSyncDate": "2026-06-02T10:15:22+00:00",
    "syncModel": "perLayer",
    "syncDirection": "bidirectional",
    "sdeType": "TwoWay",
    "sdeRole": "Parent",
    "hasConflicts": false
  },
  {
    "replicaId": "A1B2C3D4-0000-0000-0000-000000000003",
    "name": "DBO.Ags_Fs_3",
    "owner": "field.user3@example.com",
    "sourceDatabase": "mygdb2",
    "serviceUrl": "https://gis.example.com/arcgis/rest/services/Utilities/WaterNetwork/FeatureServer",
    "serviceName": "WaterNetwork",
    "creationDate": "2026-07-01T08:00:00+00:00",
    "lastSyncDate": null,
    "syncModel": "perLayer",
    "syncDirection": "bidirectional",
    "sdeType": "TwoWay",
    "sdeRole": "Parent",
    "hasConflicts": true
  }
];